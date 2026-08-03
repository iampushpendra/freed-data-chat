# FREED Data Chat

A chat website where FREED staff ask data questions in plain English and get
answers pulled live from the Postgres warehouse (the same data Metabase
db 27 reads). The LLM is OpenAI (bring-your-own key); access is gated by a
single shared password. This is a **new, separate Vercel project** — it is
never deployed by, and never interferes with, the VM's existing dashboard
auto-deploy.

## Architecture

```
Browser ──password cookie──> Vercel "freed-data-chat"
   chat UI (static)             /api/chat  (serverless, Node)
                                   │  OpenAI tool-calling loop (run_sql)
                                   ▼
                            VM (34.100.177.202) nginx vhost → query API :8157 (127.0.0.1:8158)
                                Bearer secret · single-statement SELECT/WITH only
                                read-only PG role · statement_timeout 60s
                                row cap 2,000 · JSON-lines audit log
                                   ▼
                            Postgres warehouse (freed_app_db)
```

- **`web/index.html`** — single self-contained page: password login, chat
  view, markdown replies, result tables with CSV download and a collapsible
  SQL disclosure.
- **`api/login.js`** — checks `CHAT_PASSWORD`, sets a signed HttpOnly cookie.
- **`api/chat.js`** — verifies the cookie, then runs an OpenAI tool-calling
  loop (system prompt = `context/schema.md` + rules) that calls `run_sql`
  against the VM query API, up to 3 tool calls / 60s per message.
- **`vm/query_api.py`** — stdlib-only Python HTTP server on the VM. Validates
  SQL is a single read-only `SELECT`/`WITH` statement, runs it against
  Postgres as a read-only role, caps rows, logs every request.
- **`context/schema.md`** — distilled warehouse schema + business glossary
  the model uses to write correct SQL.

## Quick start

### 1. Provision the VM query API

```bash
scripts/deploy_vm.sh
```

Copies `vm/query_api.py`, `vm/setup.sh`, the systemd unit, and the nginx
vhost to the VM, runs `setup.sh` there, restarts the `freed-chat-query`
service, and verifies `GET /healthz` from the Mac afterward.

### 2. Configure and deploy the Vercel project

```bash
scripts/set_vercel_env.sh   # prompts for each env var, pushes to Vercel (production)
vercel --prod
```

`set_vercel_env.sh` only touches this project's Vercel env — it never runs
against or redeploys the separate dashboard project.

### 3. Verify end-to-end

```bash
test/e2e.sh https://freed-data-chat.vercel.app
test/test_vm_query.sh   # needs QUERY_API_URL + QUERY_API_SECRET exported
```

## Environment variables

### Vercel (serverless, `api/*.js`)

| Var | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | OpenAI key used for the chat completions tool loop |
| `OPENAI_MODEL` | no | `gpt-4o` | chat-completions model id |
| `CHAT_PASSWORD` | yes | — | shared password gating the whole app |
| `CHAT_COOKIE_SECRET` | yes | — | HMAC key signing the `fdc_auth` cookie |
| `QUERY_API_URL` | yes | `http://34.100.177.202:8157/query` | VM query API endpoint |
| `QUERY_API_SECRET` | yes | — | bearer token the VM query API requires |

### VM (`/etc/freed/chat-query.env`)

| Var | Required | Default | Notes |
|---|---|---|---|
| `PG_HOST` | yes | — | Postgres host (warehouse, `172.20.12.9`) |
| `PG_PORT` | yes | — | Postgres port (`5432`) |
| `PG_DB` | yes | — | `freed_app_db` |
| `PG_USER` | yes | — | read-only role, e.g. `freedbi_user`/`chat_ro` |
| `PG_PASSWORD` | yes | — | password for `PG_USER` |
| `QUERY_API_SECRET` | yes | — | must match Vercel's `QUERY_API_SECRET` |
| `ROW_CAP` | no | `2000` | max rows returned per query |
| `STATEMENT_TIMEOUT` | no | `60s` | Postgres `statement_timeout` per connection |
| `BIND_HOST` | no | `127.0.0.1` | query API listens locally; nginx exposes it publicly |
| `BIND_PORT` | no | `8158` | local port; nginx fronts it on public `8157` |

## Security model

- **Password gate**: `CHAT_PASSWORD` is checked once at `/api/login`; there
  is no per-user account system.
- **Signed bearer cookie**: `fdc_auth=<exp>.<hmacSHA256(exp, CHAT_COOKIE_SECRET)>`,
  `HttpOnly; Path=/; Max-Age=604800; SameSite=Lax; Secure`. `/api/chat`
  rejects any request with a missing, tampered, or expired cookie (401).
- **VM bearer secret**: every call from Vercel to the VM query API carries
  `Authorization: Bearer <QUERY_API_SECRET>`, checked with a constant-time
  compare; no secret, no match → 401. This is the only credential that
  crosses the public internet to the VM.
- **Read-only Postgres role**: the query API connects as a SELECT-only DB
  user and additionally issues `SET default_transaction_read_only = on` per
  connection — belt and suspenders against a validator bypass.
- **SELECT-only SQL validator**: security-critical gate on the VM. Strips
  comments/string literals, requires exactly one statement, requires the
  first keyword to be `SELECT` or `WITH`, rejects any of
  `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REVOKE/COPY/CALL/DO/
  SET/EXECUTE/VACUUM/ANALYZE/LOCK/COMMENT`/`SELECT ... INTO` as top-level
  keywords, and blocks `pg_sleep`, `dblink`, `pg_read_file`, `lo_*`. A
  trailing `;` is tolerated but any additional `;` outside a string literal
  is rejected (no stacked statements).
- **HTTP-not-HTTPS caveat**: the Vercel → VM leg (`http://34.100.177.202:8157`)
  is currently plain HTTP. The bearer secret and query payloads are visible
  to anything on the network path between Vercel and the VM's public IP.
  This is an accepted short-term risk, not a design goal.
  - **TLS upgrade path**: put the VM query API behind an nginx vhost with a
    Let's Encrypt cert (or reuse an existing FREED wildcard cert) and switch
    `QUERY_API_URL` to `https://.../query`; no code changes required beyond
    the URL. Until then, treat `QUERY_API_SECRET` as the single point of
    failure and rotate it if you suspect exposure.

## Ops

- **Logs**: every request the VM query API executes (or rejects after
  validation) is appended as one JSON line to
  `/home/ubuntu/freed-data-chat/chat-query.log` on the VM — timestamp,
  request id, SQL, row_count or error, elapsed_ms, remote IP. `ssh` in and
  `tail -f chat-query.log` to watch live traffic; grep it to audit what the
  model has been running.
- **Changing the password**: update `CHAT_PASSWORD` via
  `scripts/set_vercel_env.sh` (or `vercel env add CHAT_PASSWORD production`
  directly) and redeploy (`vercel --prod`). Existing signed cookies remain
  valid until they expire (7 days) or `CHAT_COOKIE_SECRET` is also rotated.
- **Rotating the bearer secret**: generate a new value, update it in
  **both** places — the VM's `/etc/freed/chat-query.env`
  (`QUERY_API_SECRET=...`, then `sudo systemctl restart freed-chat-query`)
  and Vercel's `QUERY_API_SECRET` env var (then `vercel --prod`). Rotate
  them together; if only one side is updated the two ends will 401 each
  other.

## Limits

- **2,000 rows** returned per query (`ROW_CAP`); a `truncated: true` flag is
  set if more rows existed.
- **60 seconds** per SQL statement (`STATEMENT_TIMEOUT`) and per `/api/chat`
  request overall.
- **3 tool calls** (`run_sql` invocations) max per chat message, so the model
  can self-correct on a SQL error once or twice but cannot loop forever.
