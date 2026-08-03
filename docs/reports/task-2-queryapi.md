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
