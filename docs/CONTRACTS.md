# FREED Data Chat — Interface Contracts (binding for all tasks)

Repo root: `/Users/pushpendrasingh/projects/freed-data-chat`

## VM query API (`vm/query_api.py`)
- Python 3.12 **stdlib http.server only** + psycopg2 (both present on VM). No pip installs.
- Listens `127.0.0.1:8158`. nginx (`vm/nginx-chatquery.conf`) exposes it publicly on `0.0.0.0:8157`.
- Auth: every request needs `Authorization: Bearer <QUERY_API_SECRET>` (constant-time compare). 401 otherwise.
- `POST /query` body `{"sql": "<string>"}` →
  - 200 `{"columns": [str], "rows": [[json-safe values]], "row_count": int, "truncated": bool, "elapsed_ms": int}`
  - 400 `{"error": "<validator message>"}` — SQL rejected before execution
  - 422 `{"error": "<postgres error message>"}` — SQL ran and failed (LLM uses this to self-correct)
  - 401 `{"error": "unauthorized"}`
- `GET /healthz` (no auth) → 200 `{"ok": true, "db": "up"|"down"}`
- Validator (security-critical): strip comments/string literals → must be exactly ONE statement; first keyword `SELECT` or `WITH`; reject any of INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REVOKE/COPY/CALL/DO/SET/EXECUTE/VACUUM/ANALYZE/LOCK/COMMENT/into (SELECT INTO) as top-level keywords outside strings; reject `pg_sleep`, `dblink`, `pg_read_file`, `lo_`. Reject multiple statements (any `;` outside strings except one trailing).
- Per-connection: `SET statement_timeout` from env (default `60s`), `SET default_transaction_read_only = on`. Rows capped at `ROW_CAP` (default 2000, fetch cap+1 to set `truncated`). Values JSON-safe: Decimal→float, date/datetime→ISO string, None→null, other→str.
- Env file `/etc/freed/chat-query.env`: `PG_HOST PG_PORT PG_DB PG_USER PG_PASSWORD QUERY_API_SECRET ROW_CAP STATEMENT_TIMEOUT BIND_HOST BIND_PORT`
- Audit log: one JSON line per request to `/home/ubuntu/freed-data-chat/chat-query.log` (ts, req id, sql, row_count/error, elapsed_ms, remote ip).
- `POST /query` body may optionally include `question` (originating natural-language question, ≤1000 chars) and `chat_id` (per-request correlation id, ≤64 chars) — logging-only metadata recorded in the audit log (`null` when absent); never validated, never interpolated into SQL, never affects execution.

## Vercel serverless (`api/*.js`)
- Node 20 CommonJS or ESM (pick one, be consistent; package.json `"type"` accordingly). No heavy deps; `openai` npm package allowed, or raw fetch.
- Env vars: `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o`), `CHAT_PASSWORD`, `CHAT_COOKIE_SECRET`, `QUERY_API_URL` (e.g. `http://34.100.177.202:8157/query`), `QUERY_API_SECRET`.
- `POST /api/login` `{password}` → on match set cookie `fdc_auth=<exp>.<hmacSHA256(exp, CHAT_COOKIE_SECRET)>` (exp = unix seconds now+7d), `HttpOnly; Path=/; Max-Age=604800; SameSite=Lax; Secure`, return `{ok:true}`. Wrong → 401 `{ok:false}`.
- `POST /api/chat` — requires valid cookie (verify HMAC + not expired; else 401).
  Body: `{messages: [{role:"user"|"assistant", content:"..."}]}` (client sends trimmed history, last 12 messages max).
  OpenAI chat-completions tool loop: tool `run_sql {sql}` → POST to `QUERY_API_URL` with bearer; feed result (or error string) back; **max 3 tool calls**; 60s budget.
  Response 200: `{reply: "<markdown>", tables: [{sql, columns, rows, row_count, truncated}]}` (tables = every successful run_sql result, in order).
  Query-API unreachable → reply explains "data backend unavailable".
- System prompt: read `context/schema.md` (bundled with function via `includeFiles` or fs read relative to repo) + rules: PostgreSQL dialect, always LIMIT, prefer aggregates, never SELECT *, use only documented tables.

## Web UI (`web/index.html`)
- Single self-contained file (inline CSS/JS, no CDN). Endpoints: `/api/login`, `/api/chat`.
- Login card shown on 401; chat view: message list, assistant markdown (minimal renderer: bold, code, lists, tables), each returned table rendered scrollable (first 200 rows) with a "Download CSV" button (client-side blob, full rows) and collapsible "SQL" disclosure.
- Aesthetic: match FREED dashboard — Hanken Grotesk (system fallback since no CDN: `-apple-system` stack is fine), dark-on-light, accent #0f766e-ish teal, clean cards. Mobile-friendly.
- Keep chat history in memory only (v1). "New chat" button. Enter to send, Shift+Enter newline. Loading indicator while /api/chat pending.

## vercel.json (repo root)
- Rewrites: `/` → `/web/index.html`. Functions default runtime; `api/chat.js` maxDuration 60.

## Warehouse facts
- Postgres 172.20.12.9:5432 db `freed_app_db`, user `freedbi_user` (already read-only BI user). Reachable ONLY from the VM.
- VM: ubuntu@172.20.4.15 (ssh key ~/installs/keys/freed-live-gcp.pem from the Mac), public IP 34.100.177.202. Ports 8155/8156 taken; 8157/8158 free.
