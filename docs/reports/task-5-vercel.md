# Task 5 — Vercel serverless backend

## Status: Done

Deliverables written:
- `api/_lib/auth.js` — `signCookie`, `verifyCookie`, `timingSafeEqual`, `parseCookie` (fdc_auth cookie helpers, HMAC-SHA256 over the expiry, timing-safe compare).
- `api/login.js` — POST-only, 405 otherwise; 500 if `CHAT_PASSWORD`/`CHAT_COOKIE_SECRET` unset; timing-safe password compare; sets `fdc_auth=<exp>.<hmac>` cookie (HttpOnly, Path=/, Max-Age=604800, SameSite=Lax, Secure) on match, else 401 `{ok:false}`.
- `api/chat.js` — verifies cookie (401 `{error:"auth"}` if missing/invalid/expired); validates body (messages array, roles user/assistant only, capped at 12 messages, 8000 chars/message); builds system prompt from `context/schema.md` (fs.readFileSync with try/catch fallback stub) plus the hard rules (Postgres dialect, single SELECT/WITH, LIMIT ≤500 unless aggregating, never modify data, say so if unanswerable, concise, ₹ for currency); OpenAI tool-calling loop (gpt-4o default, temp 0.1, `run_sql` tool) capped at 3 total `run_sql` executions with a 45s AbortController timeout per call; on backend fetch failure pushes `{error:"data backend unavailable"}` and breaks the loop after finishing that round; returns `{reply, tables}` where `tables` holds every successful `run_sql` result (sql, columns, full rows up to the API's own cap, row_count, truncated); model only sees the first 100 rows per result with a truncation note. Non-POST → 405. Whole handler wrapped in try/catch → 500 `{error}`.
- `package.json` — ESM (`"type":"module"`), Node 20 engine, zero dependencies.
- `vercel.json` — rewrite `/` → `/web/index.html`, `api/chat.js` maxDuration 60.
- `.gitignore` — added `node_modules` to the existing `.vercel` / `.env*` entries.
- `.vercelignore` — excludes `vm`, `docs/reports`, `test`; `context/schema.md` and the rest of `context/` are NOT excluded so the schema deploys with the function.

## Verification

- `node --check` passed clean on `api/login.js`, `api/chat.js`, `api/_lib/auth.js`.
- `package.json` and `vercel.json` parse as valid JSON.
- Ran `api/_lib/auth.js` interactively (ESM `--input-type=module`): sign→verify round-trip true; wrong secret false; expired cookie false; missing cookie false; multi-cookie header parsing extracts `fdc_auth` correctly.
- Did not run `api/login.js`/`api/chat.js` end-to-end (no Vercel dev server, no live OpenAI/QUERY_API credentials, out of scope per "do not deploy").

## Concerns / notes for integration

- `context/schema.md` does not exist yet in this repo (being produced by a parallel agent). `chat.js` falls back to a stub prompt if the file is missing at runtime — confirm the schema file lands at `context/schema.md` before real use, or answers will always say the schema is missing.
- `.vercelignore` follows the task's final resolved list (`vm, docs/reports, test`) rather than the earlier broader draft that also mentioned excluding all of `docs/` and `scripts/` — flagging in case the intent was actually broader.
- Password compare in `login.js` uses `timingSafeEqual` from `_lib/auth.js`, which returns `false` immediately on length mismatch before the constant-time compare — this leaks password length via timing to a very limited degree; acceptable for a shared-password internal tool but noting for the record.
- `chat.js` assumes the VM query API's JSON error bodies always have an `error` string field on 400/422; if absent it falls back to generic messages, consistent with CONTRACTS.md.
