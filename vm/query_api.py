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
  POST /query    (bearer)  -> {"sql": "<string>"}
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
AUDIT_LOG = os.getenv('AUDIT_LOG', './chat-query.log')

_audit_lock = threading.Lock()


def log(msg):
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


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
    'pg_sleep', 'dblink', 'pg_read_file', 'pg_ls_dir', 'lo_import',
    'lo_export', 'pg_terminate_backend', 'pg_cancel_backend',
)

_FORBIDDEN_KEYWORD_RES = [re.compile(r'\b' + kw + r'\b', re.IGNORECASE)
                           for kw in FORBIDDEN_KEYWORDS]
_FORBIDDEN_FUNCTION_RES = [re.compile(r'\b' + fn + r'\b', re.IGNORECASE)
                            for fn in FORBIDDEN_FUNCTIONS]
_FIRST_KEYWORD_RE = re.compile(r'^\s*(SELECT|WITH)\b', re.IGNORECASE)


def _skeletonize(sql):
    """Strip comments and quoted literals, producing a bare "skeleton" safe
    to run keyword/semicolon checks against.

    Line comments (--...) and block comments (/* ... */, nesting supported)
    are removed entirely (not replaced with a space) so that a comment used
    to split a keyword rejoins correctly, e.g. 'SEL/**/ECT' skeletonizes to
    'SELECT' — which is still just a plain SELECT and must be allowed.

    Single-quoted strings ('' escapes), double-quoted identifiers (""
    escapes), and dollar-quoted strings ($tag$...$tag$) are replaced with a
    single space each — their contents (which could contain anything,
    including semicolons or forbidden keywords) must never reach the keyword
    checks, but the literal already sits inside surrounding SQL whitespace/
    punctuation so a single space is a safe stand-in.
    """
    out = []
    i = 0
    n = len(sql)
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
            i += 1
            while i < n:
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

    return ''.join(out)


def validate_sql(sql):
    """Return None if `sql` is an acceptable single read-only statement, or
    an error string explaining why it was rejected. Never raises."""
    if sql is None or not str(sql).strip():
        return 'empty query'

    skeleton = _skeletonize(sql)

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

    def do_GET(self):
        if self.path.split('?')[0] == '/healthz':
            up = db_healthcheck()
            return self._send(200, {'ok': True, 'db': 'up' if up else 'down'})
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path.split('?')[0] != '/query':
            return self._send(404, {'error': 'not found'})
        if not self._authed():
            return self._send(401, {'error': 'unauthorized'})

        req_id = uuid.uuid4().hex[:12]
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self._send(400, {'error': 'bad Content-Length'})
        if n > 1 * 1024 * 1024:
            return self._send(400, {'error': 'body too large'})

        try:
            payload = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._send(400, {'error': 'body must be JSON'})

        sql = payload.get('sql') if isinstance(payload, dict) else None
        if not isinstance(sql, str):
            return self._send(400, {'error': '"sql" must be a string'})

        err = validate_sql(sql)
        if err:
            audit({'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                   'sql': sql, 'error': err, 'stage': 'validate',
                   'remote_ip': self._client_ip()})
            return self._send(400, {'error': err})

        t0 = time.time()
        try:
            result = run_query(sql)
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            msg = str(e).strip() or type(e).__name__
            audit({'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
                   'sql': sql, 'error': msg, 'stage': 'execute',
                   'elapsed_ms': elapsed_ms, 'remote_ip': self._client_ip()})
            return self._send(422, {'error': msg})

        elapsed_ms = int((time.time() - t0) * 1000)
        result['elapsed_ms'] = elapsed_ms
        audit({'ts': datetime.now(timezone.utc).isoformat(), 'req_id': req_id,
               'sql': sql, 'row_count': result['row_count'],
               'truncated': result['truncated'], 'elapsed_ms': elapsed_ms,
               'remote_ip': self._client_ip()})
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
