# FREED Data Chat — Design

**Date:** 2026-08-04 · **Status:** Approved (approach A)

## Goal

A chat website where FREED staff ask data questions in plain English and get
answers from the Postgres warehouse (the same data Metabase db 27 reads).
LLM: OpenAI (user's key). Hosting: a **new, separate Vercel project** —
never the dashboard project the VM auto-deploys. Access: shared password.

## Architecture

```
Browser ──password cookie──> Vercel "freed-data-chat"
   chat UI (static)             /api/chat  (serverless, Node)
                                   │  OpenAI tool-calling loop (run_sql)
                                   ▼
                            VM (34.100.177.202) nginx vhost → FastAPI :8157 (127.0.0.1)
                                Bearer secret · single-statement SELECT/WITH only
                                read-only PG role chat_ro · statement_timeout 60s
                                row cap 2,000 · JSON-lines audit log
                                   ▼
                            Postgres warehouse
```

## Components

- `web/` — single-page chat UI (plain HTML/JS, dashboard aesthetic), login
  screen, result tables with CSV download, streamed answers.
- `api/login.js` — checks `CHAT_PASSWORD` env var, sets signed HttpOnly cookie
  (HMAC with `CHAT_COOKIE_SECRET`).
- `api/chat.js` — verifies cookie; OpenAI loop: system prompt = distilled
  schema + glossary; model calls `run_sql` → POST to VM `/query` with
  `QUERY_API_SECRET`; ≤3 tool calls per message; returns text + result tables.
- VM `query_api.py` — FastAPI `POST /query {sql}`: single-statement
  SELECT/WITH validator (security-critical, unit-tested), runs as `chat_ro`,
  row cap, per-request id, audit log. Systemd + nginx vhost on a new port,
  same pattern as callhistory (but PUBLIC bind + bearer auth, like the
  call-analysis vhosts).
- `context/schema.md` — distilled from the FREED Power BI Wiki KB: warehouse
  tables/columns, business glossary (settlement, mandate, HOT/WARM/COLD,
  funnel), proven example SQL.

## Error handling

- SQL error → returned to the model for one self-correction; then surfaced.
- VM down/timeout → clear "data backend unavailable" message, no hanging retries.
- Every executed query audit-logged on the VM.

## Security

- Secrets: OpenAI key + VM bearer secret in Vercel env only; DB creds on VM only.
- Hard boundary = `chat_ro` Postgres role with SELECT-only grants.
- Separate Vercel project ⇒ no interference with the VM dashboard deploy.

## Testing

- Unit tests for the SQL validator.
- VM smoke test script; E2E: "how many leads signed up last week".
