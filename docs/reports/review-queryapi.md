# Security review — VM query API (Task 2)

Reviewed: `vm/query_api.py`, `vm/test_query_api.py`, `vm/chat-query.env.example` against
`docs/CONTRACTS.md` § "VM query API" and `docs/reports/task-2-queryapi.md`.

Ran `python3 vm/test_query_api.py` (Python 3.9.6, no psycopg2 — matches the report's dev
setup): **68/68 pass**, no discrepancy from the report's claimed output.

## Spec compliance: ❌

Endpoints, status codes, JSON shapes, row cap/truncated, session hardening
(`SET default_transaction_read_only = on` before every query, in `_configure_session`,
`query_api.py:272-281`), and the fail-closed startup guard (`main()` refuses to start with
no `QUERY_API_SECRET` or missing psycopg2, `query_api.py:424-430`) all match the contract.
Two concrete deviations pull this to a fail:

- The contract's SQL-function denylist says "reject ... `lo_`" (a prefix), but the code
  enumerates only `lo_import`/`lo_export` (`query_api.py:109-112`) — other `lo_` read
  primitives pass. See Important #2 below.
- The contract requires "one JSON line per request" in the audit log; unauthorized (401)
  requests are never audited at all (`query_api.py:374-378`). See Important #1 below.

## Findings

### Important

**1. 401/Unauthorized requests are never audited — `vm/query_api.py:374-378`**
```python
if not self._authed():
    return self._send(401, {'error': 'unauthorized'})
```
No `audit()` call on this path. Every other outcome (validate-reject, execute-error,
success) gets a JSON audit line; a wrong/missing/guessed bearer token gets none. This is
the one endpoint on the public internet gated only by a static bearer secret — a
brute-force or credential-stuffing run against it leaves **zero trace** in
`chat-query.log`, directly contradicting the contract's "one JSON line per request"
audit requirement and removing the only detection signal for a leaked/guessed secret.

**2. `set_config`/`current_setting` are not denylisted, letting a query bypass the `SET` keyword ban — `vm/query_api.py:103-117`**
The `SET` keyword is blocked via `\bSET\b`, but that regex does not match inside
`set_config` (the `_` after "set" breaks the trailing word boundary), and
`set_config`/`current_setting` are absent from `FORBIDDEN_FUNCTIONS`. Verified against
the actual validator:
```
$ python3 -c "
import sys; sys.path.insert(0,'vm')
from query_api import validate_sql
print(validate_sql(\"SELECT set_config('statement_timeout','0',false)\"))
print(validate_sql(\"SELECT set_config('transaction_read_only','off',false), 1\"))
"
None
None
```
Both are **allowed**. Blast radius is limited today because each request gets a fresh
connection that's closed right after (so there's no cross-request GUC leakage), but this
is a direct hole in the intent of the contract's `SET` ban — an LLM-authored query can
silently neutralize `statement_timeout` (defeating the DoS guard) or flip
`transaction_read_only` for its own statement, using a mechanism the validator was
explicitly supposed to close off.

**3. `lo_` denylist covers only 2 of the large-object functions — `vm/query_api.py:109-112`**
Contract: reject `lo_`. Code: `FORBIDDEN_FUNCTIONS` lists only `lo_import`, `lo_export`
(the filesystem-write/read pair). Verified:
```
validate_sql("SELECT lo_read(0, 0, 100)")   -> None  (allowed)
validate_sql("SELECT lo_get(12345)")        -> None  (allowed)
validate_sql("SELECT lo_open(12345, 0)")    -> None  (allowed)
validate_sql("SELECT LO_EXPORT(1,'/tmp/x')")-> "forbidden function: lo_export" (blocked)
```
`lo_get`/`lo_read` return large-object bytes directly in the query result — no filesystem
round-trip needed — so they defeat the intent of the `lo_` ban entirely for the
"exfiltrate via inline read" path, even though the "write/export to disk" path is
correctly blocked. Whether this is exploitable end-to-end depends on ACLs the read-only
DB role holds on `pg_largeobject`/individual large objects (not verified from this
review, per the task's note that DB-side read-only grants are a separate layer) — but as
a validator-layer hole it is real and directly contradicts the contract's stated rule.

### Minor

**4. Validate-stage audit records omit `elapsed_ms` — `vm/query_api.py:398-402`**
The 400/validate-reject audit line has `ts, req_id, sql, error, stage, remote_ip` but no
`elapsed_ms`, while execute-stage and success lines both include it. Contract lists
`elapsed_ms` as a field captured for requests generally; inconsistent shape across audit
line "types" makes downstream log parsing/alerting need type-specific handling.

**5. `AUDIT_LOG` is an env var not in the contract's enumerated list — `vm/query_api.py:76` vs `docs/CONTRACTS.md:17`**
Contract's env list is `PG_HOST PG_PORT PG_DB PG_USER PG_PASSWORD QUERY_API_SECRET
ROW_CAP STATEMENT_TIMEOUT BIND_HOST BIND_PORT`. The code adds `AUDIT_LOG` (defaulting to
`./chat-query.log`) rather than hardcoding the contract's fixed path
`/home/ubuntu/freed-data-chat/chat-query.log`. Functionally fine — `chat-query.env.example`
sets it to the correct path — but a literal deviation from the documented env surface,
and if `AUDIT_LOG` is ever omitted from the real `/etc/freed/chat-query.env`, the service
silently audits to the cwd-relative path instead of the contracted location.

**6. `pg_read_binary_file` / `pg_stat_file` / adminpack file functions not denylisted — `vm/query_api.py:109-112`**
Only `pg_read_file` is blocked; `pg_read_binary_file` (reads arbitrary files as bytea,
same danger class) and `pg_stat_file` (filesystem metadata/existence oracle) both pass
validate_sql. Contract only explicitly names `pg_read_file`, so this isn't a strict
contract violation, but it's an inconsistent gap in the same threat category the
denylist is clearly trying to close. Likely mitigated by DB-role permissions (these
typically require superuser or explicit `pg_read_server_files` membership), same caveat
as finding #3.

**7. Unauthorized/oversized-body responses don't drain the request body — `vm/query_api.py:377-378, 385-386`**
`protocol_version = 'HTTP/1.1'` implies keep-alive, but the 401 path and the
"body too large" 400 path both write a response without reading
`Content-Length` bytes from `self.rfile` first. On a reused connection this leaves the
un-read body sitting in the stream, so the next request line the server tries to parse
off that socket is actually leftover body bytes — connection framing breaks (the
connection self-heals by erroring/closing, so not an infinite hang, but it's a
robustness/availability nit for any legitimate client that reuses connections and gets a
401, e.g. during a secret rotation).

## What checked out fine (no bypass found)

- **Data-modifying CTEs starting with `WITH`** — explicitly probed the case named in the
  task: `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`,
  `... (INSERT INTO t(a) VALUES (1) RETURNING *) ...`, `... (UPDATE t SET a=1
  RETURNING *) ...` — all three are **rejected** ("forbidden keyword: DELETE/INSERT/
  UPDATE"). The implementer's choice to scan the *whole* skeleton for forbidden keywords
  (not just top-level/outside-subquery, an explicit design tradeoff noted in their report)
  is what saves this case — a strictly top-level-only scanner would have missed it.
- **pg_sleep, dblink, pg_terminate_backend/pg_cancel_backend** — all correctly denylisted
  and confirmed rejected by direct probe.
- **Comment/dollar-quote obfuscation to hide a semicolon or forbidden keyword** — probed
  `SELECT 1 /* ; DROP TABLE x */` and nested `$a$ $b$ DROP TABLE x $b$ $a$` — both
  skeletonize to inert content and are correctly allowed as harmless (the "danger" text is
  string/comment content, not live SQL).
- **Bearer compare** — uses `hmac.compare_digest`, gated behind cheap truthiness checks
  that don't themselves leak timing on secret content.
- **Read-only enforcement ordering** — `_configure_session()` runs before `cur.execute(sql)`
  on every connection, and `run_query`'s `finally: conn.close()` guarantees cleanup even if
  `SET` fails (e.g., a malformed `STATEMENT_TIMEOUT` env value raises before any user SQL
  runs — fails safe, confirmed by code path).
- **JSON parse errors → 400, not 500**; oversized body (>1MB) → 400; missing/invalid
  `Content-Length` → 400 — all confirmed by code path, no exception can reach an
  uncaught-500 in `do_POST`.
- **Threading** — one psycopg2 connection per request, no shared mutable state besides
  the audit-log file lock; no race condition found.

## Verdict

Spec ❌ — 0 Critical, 3 Important, 4 Minor
