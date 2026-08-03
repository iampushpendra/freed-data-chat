# Task 2 — VM query API

## Status
Done. All deliverables written, tests pass locally.

Files:
- `vm/query_api.py`
- `vm/test_query_api.py`
- `vm/chat-query.env.example`

## Test count + output tail

```
$ python3 vm/test_query_api.py
...
----------------------------------------------------------------------
Ran 68 tests in 0.002s

OK
```

68 tests, all passing: 19 "allowed" cases, 30 "rejected" cases, 3 auth-compare
cases, 16 json_safe cases.

Note: this dev machine has Python 3.9 and no psycopg2 installed. `query_api.py`
guards the `import psycopg2` at module level with try/except ImportError so
`validate_sql`/`json_safe` (and the whole test suite) can be exercised without
a DB driver present; `main()` refuses to start if psycopg2 is actually
missing. The target VM has Python 3.12 + psycopg2 per the contract.

## Design notes

- **Validator approach**: `_skeletonize()` walks the SQL char-by-char and
  removes line comments, block comments (nesting supported), single-quoted
  strings ('' escapes), double-quoted identifiers ("" escapes), and
  dollar-quoted strings (`$tag$...$tag$` and bare `$$...$$`). Comments are
  deleted with *no* replacement so a comment used to split a keyword still
  reassembles (`SEL/**/ECT` → `SELECT`, correctly treated as a plain SELECT).
  Quoted literals are replaced with a single space each, since their content
  must never be inspected but the literal already sits inside surrounding
  whitespace/punctuation.
- Multi-statement check: strip one optional trailing semicolon from the
  skeleton, then any remaining `;` rejects.
- First-keyword check: `^\s*(SELECT|WITH)\b` on the skeleton, case-insensitive.
- Forbidden top-level keywords and forbidden functions both use `\b`-bounded,
  case-insensitive regexes against the skeleton, so `created_at` doesn't trip
  `CREATE`, but `DROP/*x*/ TABLE` (which skeletonizes to `DROP TABLE`, keeping
  the space that was already there after the comment) is still caught.
- `run_query()` opens one psycopg2 connection per request, immediately runs
  `SET statement_timeout` (value regex-validated against a duration-literal
  pattern before interpolation, since `SET` doesn't support bind parameters
  for that clause) and `SET default_transaction_read_only = on`, fetches
  `ROW_CAP + 1` rows to compute `truncated`, converts every value with
  `json_safe()`, and closes the connection in `finally`.
- `json_safe()`: bool/int/float/str pass through as-is (checked before the
  general numeric branch so `True`/`False` aren't coerced), `Decimal` → float,
  `date`/`datetime` → `.isoformat()`, `bytes` → UTF-8 decode with a hex
  fallback if not valid UTF-8, anything else → `str()`.
- Audit log: one JSON line per request (validation rejection, execution
  error, or success) to `AUDIT_LOG` (env, default `./chat-query.log`; VM
  systemd env file points it at `/home/ubuntu/freed-data-chat/chat-query.log`
  per the contract). Audit writes are wrapped so a log-write failure never
  breaks the response.
- `GET /healthz` needs no bearer auth and opens its own short-lived
  connection to run `SELECT 1`.
- Followed the `callhistory_api.py` house style: module docstring documenting
  the security model up front, `ThreadingHTTPServer`, silenced
  `BaseHTTPRequestHandler.log_message`, IST-timestamped `log()` helper, env
  vars read directly from `os.getenv`.

## Concerns

- `STATEMENT_TIMEOUT` is validated against a simple duration-literal regex
  (`[0-9]+(ms|s|min|h|d)?`) before being interpolated into `SET statement_timeout
  = '...'` — this is deliberately conservative (no bind-param support for SET)
  but means an operator-supplied env value outside that pattern makes the
  service refuse every query at runtime (fails safe, but worth knowing).
- The word-boundary forbidden-keyword scan runs over the *whole* statement,
  not strictly "top-level" (e.g. outside subqueries) — the contract's own
  language is ambiguous here ("reject ... as top-level keywords"), and
  scanning the whole skeleton is strictly more conservative, so I chose that
  over building a real top-level/depth-aware parser.
- No live DB test was run (no VM access from here, and the task explicitly
  said don't ssh anywhere) — `run_query`/`_configure_session`/`db_healthcheck`
  are exercised only by code review and `py_compile`, not by a real Postgres
  round-trip.

---

# Addendum — review fixes (round 2)

Applied against `vm/query_api.py` and `vm/test_query_api.py` only. No commit, no deploy.

## Changes

**Important #1 — 401/400 requests missing from audit log**
`do_POST` rewritten so every response path emits exactly one `audit()` call: 401
unauthorized, 400 (body too large, body-must-be-JSON, `"sql"` must be a string,
validator reject), 422 execute error, and 200 success. `t0` is now captured at the very
start of `do_POST` (not after validation), so `elapsed_ms` is present and consistent on
every audit record, including the 401 line. The 401 audit record intentionally omits the
attempted `sql`/secret — it logs only `ts`, `remote_ip`, `path`, `outcome:
"unauthorized"`, `elapsed_ms`.

**Important #2 — `set_config`/`current_setting` bypass of the `SET` ban**
Added `set_config` and `current_setting` to `FORBIDDEN_FUNCTIONS`. Both are now rejected
anywhere in the skeleton, closing the `\bSET\b`-regex gap (the trailing `_` in
`set_config` breaks the `SET` keyword's word boundary).

**Important #3 — `lo_` prefix under-enumerated**
Kept the existing `lo_import`/`lo_export` entries (so their specific error messages
still fire first) and added a general backstop: `_LO_PREFIX_RE = r'\blo_[a-z_]*\s*\('`
rejects any `lo_<ident>(` call — `lo_read`, `lo_get`, `lo_open`, `lo_creat`,
`lo_from_bytea`, etc. — plus a dedicated `loread`/`lowrite` check for the two legacy
aliases that don't follow the `lo_` naming convention. A bare column reference like
`lo_balance` (no call parens) is intentionally still allowed.

**Minor — additional file-reading functions**
Added `pg_read_binary_file` and `pg_stat_file` to `FORBIDDEN_FUNCTIONS` (`pg_ls_dir` and
`pg_read_file` were already present).

**Minor — validate-stage audit missing `elapsed_ms`**
Fixed as part of the Important #1 rewrite — every audit stage now includes it.

**Minor — body not drained before 401/400/oversized responses**
Added `Handler._drain_body(n)`, which reads and discards up to `n` (`Content-Length`)
bytes from `self.rfile` before writing an error response, so a reused HTTP/1.1
keep-alive connection doesn't have leftover body bytes desync the next request's
framing. Called before the 404 (wrong path), 401 (unauthorized), and 400 (body-too-large)
responses. The one case where the byte count itself is unknown — an unparseable
`Content-Length` header — can't be safely drained, so that path instead sets
`self.close_connection = True` to force the connection closed rather than guess.

**AUDIT_LOG reconciliation**
`AUDIT_LOG` default remains `./chat-query.log` (unchanged) — confirmed this already
resolves to the contract's `/home/ubuntu/freed-data-chat/chat-query.log` because
`vm/freed-chat-query.service` sets `WorkingDirectory=/home/ubuntu/freed-data-chat`. Added
a code comment at the `AUDIT_LOG` definition documenting this reconciliation. Env
override is untouched.

## CRITICAL finding folded in mid-task: E'' escape-string statement smuggling

`_skeletonize()`'s single-quoted-string scanner only understood `''` as an escaped quote
and had no notion of PostgreSQL `E'...'` backslash-escape strings. In a real Postgres
parse, `\'` inside an `E''` string does not close the string — but the validator's naive
scanner didn't know that, so it could desync from Postgres's real tokenization and let a
second statement slip past the single-statement / forbidden-function checks. Confirmed
exploit that previously returned `None` (allowed):

```sql
SELECT E'\''; SELECT pg_terminate_backend(12345); --'
```

Fix (belt-and-suspenders, both applied):

1. `_skeletonize` now detects an `E'`/`e'` string-open (word-boundary before the `E`) and,
   for that literal only, treats `\` as an escape character while scanning past it (so
   `\'` and `\\` are handled the way Postgres actually parses them). It returns
   `(skeleton, has_estring)` instead of just the skeleton string.
2. `validate_sql` rejects outright whenever `has_estring` is true, with message
   `"E'' escape-string literals are not allowed"` — this removes the whole ambiguity
   class regardless of scanner precision, since this read-only analytics API has no
   legitimate need for `E''` strings.

Verified now rejected: `SELECT E'hello'`, `SELECT e'hello'`, the exploit string above,
and `SELECT E'a\\b'`.

Verified still allowed (no false positives introduced): `SELECT * FROM t WHERE code =
'E'` (plain string whose content is the letter E) and `SELECT * FROM t WHERE name LIKE
'a\_b'` (backslash in an ordinary, non-`E`, string).

## Correctness fix folded in mid-task: `json_safe` jsonb/array columns

`json_safe()` fell through `dict`/`list` values (from psycopg2-decoded `jsonb`/`json`/
array columns) to the generic `str(value)` branch, which produces a Python `repr` string
instead of valid embedded JSON. Added an explicit `isinstance(value, (dict, list))`
branch that returns the value unchanged — psycopg2 already hands back JSON-safe
dict/list structures.

## Exploit strings now rejected (full list, new tests)

```
SELECT set_config('statement_timeout', '0', false)
SELECT set_config('transaction_read_only', 'off', false), 1
SELECT current_setting('statement_timeout')
SELECT lo_read(0, 0, 100)
SELECT lo_get(12345)
SELECT lo_open(12345, 0)
SELECT lo_creat(-1)
SELECT lo_from_bytea(0, '\x00')
SELECT loread(0, 100)
SELECT lowrite(0, '\x00')
SELECT pg_read_binary_file('/etc/passwd')
SELECT pg_stat_file('/etc/passwd')
SELECT E'hello'
SELECT e'hello'
SELECT E'\''; SELECT pg_terminate_backend(12345); --'
SELECT E'a\\b'
```

All previously-allowed cases (plain SELECT/WITH, subqueries, string/comment/dollar-quote
obfuscation, quoted identifiers, `created_at`-style column names, etc.) were re-verified
to still pass — plus new coverage for `lo_balance` (bare column, not a call), a
single-char `'E'` string literal, and a plain-string backslash (`LIKE 'a\_b'`).

## Test run (round 2)

`python3 vm/test_query_api.py` — **90/90 pass** (up from the earlier 68). Tail of output:

```
test_truncate (__main__.TestValidateSqlRejected) ... ok
test_update (__main__.TestValidateSqlRejected) ... ok
test_vacuum (__main__.TestValidateSqlRejected) ... ok
test_whitespace_only (__main__.TestValidateSqlRejected) ... ok

----------------------------------------------------------------------
Ran 90 tests in 0.002s

OK
```
