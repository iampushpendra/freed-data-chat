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

## Review fixes (docs/reports/review-vercel.md)

Applied to `api/chat.js`, `api/login.js`, `vercel.json`, `package.json` only. No commit, no `vercel build`/deploy performed.

- **Critical — empty reply on backend-down.** `chat.js`: added `BACKEND_DOWN_REPLY` constant. When `backendDown` is set, the handler now sets `lastAssistantContent = BACKEND_DOWN_REPLY` unconditionally after the loop (no longer relies on the model getting a turn), and any `tables` already collected before the failure are still returned. Reply is never empty in this path.
- **Critical/Important — timeout budget could exceed 60s.** `chat.js`: (a) `TOOL_TIMEOUT_MS` reduced 45000 → 15000 (VM-fetch AbortController in `runSql`); (b) added a new 20000ms AbortController timeout to `callOpenAI`'s fetch, with a friendly "OpenAI request timed out" error on abort; (c) added an overall deadline `const deadline = Date.now() + OVERALL_BUDGET_MS` (55000ms) captured once at the point the tool loop starts. The round loop and the per-tool-call loop both check `Date.now() >= deadline` and stop (marking `deadlineExceeded`), returning whatever `reply`/`tables` exist so far — worst case 3×15s (run_sql) + OpenAI round-trips capped at 20s each stays under the 55s internal budget, safely inside vercel.json's `maxDuration: 60`. `maxDuration` left at 60.
- **Important — empty reply when tool-call budget exhausted on final round.** `chat.js`: added `needsFinalCompletion` flag, set true whenever the latest assistant turn ended in `tool_calls` rather than final text. After the loop, if `needsFinalCompletion` is true and neither `backendDown` nor `deadlineExceeded` (and time remains), the handler makes exactly one additional `callOpenAI(..., allowTools=false)` call — `callOpenAI` now takes an `allowTools` param that omits `tools` and sets `tool_choice: 'none'` when false — forcing the model to produce closing text summarizing whatever `tables` were collected.
- **Important — login.js unwrapped handler.** `api/login.js`: wrapped the entire handler body in try/catch mirroring `chat.js`'s convention; unexpected exceptions now return `500 {error: err?.message || 'internal error'}` instead of Vercel's generic error page.
- **Minor — pin schema file into bundle.** `vercel.json`: added `"includeFiles": "context/schema.md"` to the existing `api/chat.js` functions entry (merged, `maxDuration: 60` untouched).
- **Minor — log schema fallback.** `chat.js` `loadSchema()`: added `console.warn('[chat.js] context/schema.md not found; using FALLBACK_SCHEMA stub')` in the catch branch so a missing schema is visible in Vercel logs.
- **Minor — malformed/empty OpenAI final message.** `chat.js`: added a final non-empty-reply guarantee after all other logic — if `lastAssistantContent` is still falsy, it's set to a friendly message that differs depending on whether any `tables` were collected ("...couldn't generate a summary in time..." vs. "...wasn't able to generate a response in time..."). Covers both the malformed-OpenAI-response case and the deadline-exceeded-before-any-text case.
- **Minor — package.json engines.** Bumped `"node": "20.x"` → `"22.x"` (Vercel deprecates 20.x builds after 2026-10-01).

Kept unchanged: 3-run_sql cap (`MAX_TOOL_CALLS`), cookie auth flow, message validation (12-message/8000-char caps), tool/threading message format, no secrets in output, `.vercelignore` schema inclusion.

### Verification

- `node --check api/chat.js` → passed (`CHAT_OK`).
- `node --check api/login.js` → passed (`LOGIN_OK`).
- `vercel.json` parses as valid JSON (`VERCEL_JSON_OK`).
- `package.json` parses as valid JSON (`PACKAGE_JSON_OK`).
- Did not run `vercel build` or deploy, per task constraints.
