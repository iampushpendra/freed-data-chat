#!/usr/bin/env python3
"""FREED Data Chat — VM query API. Runs on the VM, executes read-only SQL
against the Postgres warehouse on behalf of the Vercel `/api/chat` serverless
function's OpenAI tool-calling loop.

Security model
  This process is the ONLY thing standing between an LLM-generated SQL string
  and the production warehouse. Two independent layers, both required:

  1. `validate_sql()` — a SECURITY-CRITICAL text-level gate. It strips
     comments/string/identifier/dollar-quoted literals into a bare "skeleton"
     and rejects anything that is not a single SELECT/WITH statement, plus a
     denylist of dangerous keywords and functions. This is defense before the
     query ever reaches Postgres, and it is intentionally conservative: it
     does not attempt to be a real SQL parser, it just refuses to let obvious
     danger through.
  2. Postgres-side containment — every connection additionally runs with
     `default_transaction_read_only = on` and a `statement_timeout`, and (per
     the design doc) the DB role itself (`chat_ro`) is expected to hold
     SELECT-only grants. Either layer alone would be insufficient; together
     they mean a validator bug still can't mutate data or hang the DB.

  Binds to 127.0.0.1 only — nginx (vm/nginx-chatquery.conf) is the public
  front door and is the thing enforcing network exposure on :8157. Bearer
  auth here is a second factor, compared with hmac.compare_digest to avoid
  timing side-channels.

Endpoints
  GET  /healthz  (no auth) -> {"ok": true, "db": "up"|"down"}
  POST /query    (bearer)  -> {"sql": "<string>",
                               "question": "<string, optional>",
                               "chat_id": "<string, optional>"}
                 "question"/"chat_id" are OPTIONAL logging-only metadata (the
                 originating natural-language question and a per-request
                 correlation id from the Vercel chat handler): they are never
                 interpolated into SQL and never affect validation or
                 execution, only the audit-log record. Older/other callers
                 that omit them are unaffected.
                 200 {"columns", "rows", "row_count", "truncated", "elapsed_ms"}
                 400 {"error": "<validator message>"}   SQL rejected before execution
                 422 {"error": "<postgres error message>"}  SQL ran and failed
                 401 {"error": "unauthorized"}

Env (see chat-query.env.example; systemd EnvironmentFile provides these as
plain process environment — this file does not parse the env file itself)
  PG_HOST PG_PORT PG_DB PG_USER PG_PASSWORD   Postgres connection
  QUERY_API_SECRET                            bearer token, required
  ROW_CAP             default 2000            rows returned before truncation
  STATEMENT_TIMEOUT   default 60s             passed verbatim to `SET statement_timeout`
  BIND_HOST           default 127.0.0.1
  BIND_PORT           default 8158
  AUDIT_LOG           default ./chat-query.log   JSON-lines audit log path
"""
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - only required to actually serve traffic
    psycopg2 = None

IST = timezone(timedelta(hours=5, minutes=30))

PG_HOST = os.getenv('PG_HOST', '127.0.0.1')
PG_PORT = int(os.getenv('PG_PORT', '5432'))
PG_DB = os.getenv('PG_DB', '')
PG_USER = os.getenv('PG_USER', '')
PG_PASSWORD = os.getenv('PG_PASSWORD', '')
QUERY_API_SECRET = os.getenv('QUERY_API_SECRET', '')
ROW_CAP = int(os.getenv('ROW_CAP', '2000'))
STATEMENT_TIMEOUT = os.getenv('STATEMENT_TIMEOUT', '60s')
BIND_HOST = os.getenv('BIND_HOST', '127.0.0.1')
BIND_PORT = int(os.getenv('BIND_PORT', '8158'))
# Default is relative on purpose: the systemd unit (freed-chat-query.service)
# sets WorkingDirectory=/home/ubuntu/freed-data-chat, so './chat-query.log'
# resolves to the contract's fixed path (docs/CONTRACTS.md) without hardcoding
# it here. AUDIT_LOG remains overridable via the env file.
AUDIT_LOG = os.getenv('AUDIT_LOG', './chat-query.log')

# Sane caps for the optional "question"/"chat_id" logging-only body fields
# (see build_audit_record() below). These are metadata only: they are never
# validated as SQL-affecting input and never interpolated into any query.
QUESTION_MAX_LEN = 1000
CHAT_ID_MAX_LEN = 64

_audit_lock = threading.Lock()


def log(msg):
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _cap(value, max_len):
    """Coerce `value` to a length-capped string, or None if `value` is None.
    Used only for logging metadata -- never for anything SQL/validation
    related."""
    if value is None:
        return None
    return str(value)[:max_len]


def build_audit_record(base, question=None, chat_id=None):
    """Pure helper: return a new audit-log record dict that is `base` plus
    the optional "question"/"chat_id" logging-only metadata fields, capped
    to sane lengths. Always sets both keys explicitly (null when the caller
    didn't supply a value) so every /query audit line has a consistent
    shape. Does not mutate `base`."""
    record = dict(base)
    record['question'] = _cap(question, QUESTION_MAX_LEN)
    record['chat_id'] = _cap(chat_id, CHAT_ID_MAX_LEN)
    return record


def audit(record):
    """Append one JSON line to AUDIT_LOG. Never raises — a broken audit log
    must not take the API down."""
    line = json.dumps(record, default=str)
    try:
        with _audit_lock:
            with open(AUDIT_LOG, 'a') as f:
                f.write(line + '\n')
    except OSError as e:
        log(f"WARNING: audit log write failed: {e}")


# --------------------------------------------------------------------------
# SQL validator — security-critical. See module docstring.
# --------------------------------------------------------------------------

_DOLLAR_TAG_RE = re.compile(r'\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$')

FORBIDDEN_KEYWORDS = (
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE',
    'GRANT', 'REVOKE', 'COPY', 'CALL', 'DO', 'SET', 'EXECUTE', 'VACUUM',
    'ANALYZE', 'LOCK', 'COMMENT', 'INTO',
)

FORBIDDEN_FUNCTIONS = (
    'pg_sleep', 'dblink', 'pg_read_file', 'pg_read_binary_file',
    'pg_stat_file', 'pg_ls_dir', 'lo_import', 'lo_export',
    'pg_terminate_backend', 'pg_cancel_backend', 'set_config',
    'current_setting',
)

_FORBIDDEN_KEYWORD_RES = [re.compile(r'\b' + kw + r'\b', re.IGNORECASE)
                           for kw in FORBIDDEN_KEYWORDS]
_FORBIDDEN_FUNCTION_RES = [re.compile(r'\b' + fn + r'\b', re.IGNORECASE)
                            for fn in FORBIDDEN_FUNCTIONS]
_FIRST_KEYWORD_RE = re.compile(r'^\s*(SELECT|WITH)\b', re.IGNORECASE)

# The enumerated FORBIDDEN_FUNCTIONS list above only names the large-object
# write/export pair (lo_import/lo_export) explicitly, but Postgres exposes a
# whole family of lo_* primitives (lo_read, lo_get, lo_open, lo_creat,
# lo_from_bytea, ...) that can read/exfiltrate large-object data without ever
# touching the filesystem. Reject the prefix generally, plus the two legacy
# large-object aliases that don't follow the lo_ naming convention.
_LO_PREFIX_RE = re.compile(r'\blo_[a-z_]*\s*\(', re.IGNORECASE)
_LOREAD_LOWRITE_RE = re.compile(r'\b(loread|lowrite)\b', re.IGNORECASE)


def _skeletonize(sql):
    """Strip comments and quoted literals, producing a bare "skeleton" safe
    to run keyword/semicolon checks against. Returns (skeleton, has_estring).

    Line comments (--...) and block comments (/* ... */, nesting supported)
    are removed entirely (not replaced with a space) so that a comment used
    to split a keyword rejoins correctly, e.g. 'SEL/**/ECT' skeletonizes to
    'SELECT' — which is still just a plain SELECT and must be allowed.

    Single-quoted strings, double-quoted identifiers (\"\" escapes), and
    dollar-quoted strings ($tag$...$tag$) are replaced with a single space
    each — their contents (which could contain anything, including
    semicolons or forbidden keywords) must never reach the keyword checks,
    but the literal already sits inside surrounding SQL whitespace/
    punctuation so a single space is a safe stand-in.

    Plain '' strings only recognize '' as an escaped quote. PostgreSQL
    `E'...'` strings additionally treat a backslash as an escape character
    (so `\\'` does not close the string), which a naive '' scanner does not
    know about — that mismatch between this lexer and Postgres's real parser
    is exactly the kind of desync that can smuggle a second statement past
    the single-statement check. This scanner detects an E'/e' string prefix
    (word-boundary before the E) and, for that literal only, honors
    backslash escapes while scanning past it. Callers reject the query
    outright whenever `has_estring` comes back true (E-strings are simply
    not needed for the read-only analytics queries this API accepts).
    """
    out = []
    i = 0
    n = len(sql)
    has_estring = False
    while i < n:
        c = sql[i]

        if sql[i:i + 2] == '--':
            j = sql.find('\n', i)
            if j == -1:
                i = n
            else:
                out.append('\n')
                i = j + 1
            continue

        if sql[i:i + 2] == '/*':
            depth = 1
            i += 2
            while i < n and depth > 0:
                if sql[i:i + 2] == '/*':
                    depth += 1
                    i += 2
                elif sql[i:i + 2] == '*/':
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue

        if c == "'":
            is_estring = False
            if i > 0 and sql[i - 1] in ('E', 'e'):
                prev2 = sql[i - 2] if i >= 2 else ''
                if not (prev2.isalnum() or prev2 == '_'):
                    is_estring = True
                    has_estring = True
            i += 1
            while i < n:
                if is_estring and sql[i] == '\\':
                    i += 2
                    continue
                if sql[i] == "'":
                    if sql[i:i + 2] == "''":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                i = n
            out.append(' ')
            continue

        if c == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if sql[i:i + 2] == '""':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                i = n
            out.append(' ')
            continue

        if c == '$':
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                if j == -1:
                    i = n
                else:
                    i = j + len(tag)
                out.append(' ')
                continue

        out.append(c)
        i += 1

    return ''.join(out), has_estring


def validate_sql(sql):
    """Return None if `sql` is an acceptable single read-only statement, or
    an error string explaining why it was rejected. Never raises."""
    if sql is None or not str(sql).strip():
        return 'empty query'

    skeleton, has_estring = _skeletonize(sql)

    if has_estring:
        return "E'' escape-string literals are not allowed"

    body = skeleton.strip()
    if body.endswith(';'):
        body = body[:-1].rstrip()
    if ';' in body:
        return 'only a single SQL statement is allowed'

    if not _FIRST_KEYWORD_RE.match(body):
        return 'query must start with SELECT or WITH'

    for rx in _FORBIDDEN_KEYWORD_RES:
        if rx.search(body):
            return f'forbidden keyword: {rx.pattern[2:-2]}'

    for rx in _FORBIDDEN_FUNCTION_RES:
        if rx.search(skeleton):
            return f'forbidden function: {rx.pattern[2:-2]}'

    if _LO_PREFIX_RE.search(skeleton):
        return 'forbidden function: lo_*'

    if _LOREAD_LOWRITE_RE.search(skeleton):
        return 'forbidden function: loread/lowrite'

    return None


# --------------------------------------------------------------------------
# JSON-safe value conversion
# --------------------------------------------------------------------------

def json_safe(value):
    """Convert a raw psycopg2 row value into something json.dumps can emit
    per the contract: Decimal -> float, date/datetime -> ISO string,
    None -> null, bytes/other -> str."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (dict, list)):
        # psycopg2 already decodes json/jsonb columns into plain dict/list
        # (and array columns into list) — these are already JSON-safe and
        # must be passed through, not stringified into a Python repr.
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.hex()
    return str(value)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def _configure_session(conn):
    """SET statement_timeout / read-only for this connection. statement_timeout
    accepts a GUC value like '60s' — SET does not support bind parameters for
    that clause in all forms, so validate it is a simple duration string
    before interpolating."""
    if not re.fullmatch(r"[0-9]+(ms|s|min|h|d)?", STATEMENT_TIMEOUT.strip(), re.IGNORECASE):
        raise ValueError(f'invalid STATEMENT_TIMEOUT: {STATEMENT_TIMEOUT!r}')
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        cur.execute("SET default_transaction_read_only = on")


def run_query(sql):
    """Execute `sql` (already validated) and return the contract's success
    shape. Raises psycopg2 errors on failure — caller turns those into 422."""
    conn = _connect_only()
    try:
        _configure_session(conn)
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(ROW_CAP + 1)
            truncated = len(rows) > ROW_CAP
            if truncated:
                rows = rows[:ROW_CAP]
            safe_rows = [[json_safe(v) for v in row] for row in rows]
        conn.rollback()  # read-only txn; nothing to commit, but close it cleanly
        return {
            'columns': columns,
            'rows': safe_rows,
            'row_count': len(safe_rows),
            'truncated': truncated,
        }
    finally:
        conn.close()


def _connect_only():
    if psycopg2 is None:
        raise RuntimeError('psycopg2 is not installed')
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASSWORD,
        connect_timeout=10,
    )


def db_healthcheck():
    try:
        conn = _connect_only()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
            return True
        finally:
            conn.close()
    except Exception as e:
        log(f"healthz: db down: {type(e).__name__}: {e}")
        return False


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'freed-chat-query/1.0'

    def log_message(self, *a):
        pass  # structured logging happens via log()/audit() instead

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self):
        auth = self.headers.get('Authorization') or ''
        got = auth[7:] if auth.startswith('Bearer ') else ''
        return bool(got) and bool(QUERY_API_SECRET) and hmac.compare_digest(got, QUERY_API_SECRET)

    def _client_ip(self):
        fwd = self.headers.get('X-Forwarded-For')
        if fwd:
            return fwd.split(',')[0].strip()
        return self.client_address[0]

    def _drain_body(self, n):
        """Read and discard up to `n` already-announced Content-Length bytes
        before writing an error response. On a keep-alive (HTTP/1.1)
        connection, writing a response without first consuming the body the
        client already sent leaves those bytes sitting in the socket buffer;
        the server then misparses them as the start of the next request
        line, corrupting framing for the next request on the same
        connection. If draining fails partway, force the connection closed
        instead of leaving it in an unknown state."""
        remaining = n
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except Exception:
            self.close_connection = True

    def do_GET(self):
        if self.path.split('?')[0] == '/healthz':
            up = db_healthcheck()
            return self._send(200, {'ok': True, 'db': 'up' if up else 'down'})
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        t0 = time.time()
        path = self.path.split('?')[0]
        remote_ip = self._client_ip()

        def elapsed_ms():
            return int((time.time() - t0) * 1000)

        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            # Length is unknown, so it can't be safely drained -- force the
            # connection closed rather than leave keep-alive framing in an
            # ambiguous state.
            self.close_connection = True
            return self._send(400, {'error': 'bad Content-Length'})

        if path != '/query':
            self._drain_body(n)
            return self._send(404, {'error': 'not found'})

        if not self._authed():
            self._drain_body(n)
            audit({'ts': datetime.now(timezone.utc).isoformat(),
                   'remote_ip': remote_ip, 'path': path,
                   'outcome': 'unauthorized', 'elapsed_ms': elapsed_ms()})
            return self._send(401, {'error': 'unauthorized'})

        req_id = uuid.uuid4().hex[:12]

        if n > 1 * 1024 * 1024:
            self._drain_body(n)
            audit({'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                   'error': 'body too large', 'stage': 'validate',
                   'elapsed_ms': elapsed_ms(), 'remote_ip': remote_ip})
            return self._send(400, {'error': 'body too large'})

        try:
            payload = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            audit({'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                   'error': 'body must be JSON', 'stage': 'validate',
                   'elapsed_ms': elapsed_ms(), 'remote_ip': remote_ip})
            return self._send(400, {'error': 'body must be JSON'})

        sql = payload.get('sql') if isinstance(payload, dict) else None
        # "question"/"chat_id" are OPTIONAL logging-only metadata (see module
        # docstring). They are read here purely to attach to the audit
        # record via build_audit_record() -- they never influence validation
        # or execution below, and a non-string value is simply dropped
        # rather than rejected.
        question = payload.get('question') if isinstance(payload, dict) else None
        if not isinstance(question, str):
            question = None
        chat_id = payload.get('chat_id') if isinstance(payload, dict) else None
        if not isinstance(chat_id, str):
            chat_id = None

        if not isinstance(sql, str):
            audit(build_audit_record({
                'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                'error': '"sql" must be a string', 'stage': 'validate',
                'elapsed_ms': elapsed_ms(), 'remote_ip': remote_ip},
                question=question, chat_id=chat_id))
            return self._send(400, {'error': '"sql" must be a string'})

        err = validate_sql(sql)
        if err:
            audit(build_audit_record({
                'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                'sql': sql, 'error': err, 'stage': 'validate',
                'elapsed_ms': elapsed_ms(), 'remote_ip': remote_ip},
                question=question, chat_id=chat_id))
            return self._send(400, {'error': err})

        try:
            result = run_query(sql)
        except Exception as e:
            msg = str(e).strip() or type(e).__name__
            audit(build_audit_record({
                'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                'sql': sql, 'error': msg, 'stage': 'execute',
                'elapsed_ms': elapsed_ms(), 'remote_ip': remote_ip},
                question=question, chat_id=chat_id))
            return self._send(422, {'error': msg})

        result['elapsed_ms'] = elapsed_ms()
        audit(build_audit_record({
            'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
            'sql': sql, 'row_count': result['row_count'],
            'truncated': result['truncated'], 'elapsed_ms': result['elapsed_ms'],
            'remote_ip': remote_ip},
            question=question, chat_id=chat_id))
        return self._send(200, result)


def main():
    if not QUERY_API_SECRET:
        log('FATAL: QUERY_API_SECRET is not set. Refusing to start with no auth.')
        return 1
    if psycopg2 is None:
        log('FATAL: psycopg2 is not installed in this interpreter.')
        return 1

    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    srv.daemon_threads = True
    log(f'listening on http://{BIND_HOST}:{BIND_PORT} '
        f'(row_cap={ROW_CAP}, statement_timeout={STATEMENT_TIMEOUT}, audit_log={AUDIT_LOG})')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
