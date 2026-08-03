# Task 6 — docs, e2e test, VM test, Vercel env helper

**Status:** done

## Deliverables

- `README.md` — overview, ASCII architecture (from DESIGN.md), quick start
  (`scripts/deploy_vm.sh`, then `scripts/set_vercel_env.sh` + `vercel --prod`),
  env var tables for both Vercel and VM sides, security model (password gate,
  signed cookie, VM bearer secret, read-only PG role, SELECT-only validator,
  HTTP-not-HTTPS caveat + TLS upgrade path), ops (chat-query.log location,
  changing the password, rotating the bearer secret), limits (2000 rows, 60s,
  3 tool calls).
- `scripts/set_vercel_env.sh` — no-arg helper; for each of
  `OPENAI_API_KEY OPENAI_MODEL CHAT_PASSWORD CHAT_COOKIE_SECRET
  QUERY_API_URL QUERY_API_SECRET`, uses an already-exported value silently if
  present, else prompts (with sensible defaults for `CHAT_COOKIE_SECRET` via
  `openssl rand -hex 32`, `QUERY_API_URL`, `OPENAI_MODEL`); `vercel env rm ...
  -y` before each `add` (rm failure tolerated); `set -uo pipefail`.
- `test/e2e.sh` — `BASE_URL` from `$1` or `https://freed-data-chat.vercel.app`,
  `CHAT_PASSWORD` from env. Step 0 (conditional on `QUERY_API_URL` exported):
  hits `${QUERY_API_URL%/query}/healthz` and expects `ok:true`. Steps 1-4 per
  spec: wrong password 401, right password + cookie capture, chat without
  cookie 401, chat with cookie 200 + non-empty `.reply` + `.tables[0].row_count
  >= 1`. Uses `jq` if present, else a `python3 -c` fallback parser. PASS/FAIL
  per step, non-zero exit on any FAIL.
- `test/test_vm_query.sh` — needs `QUERY_API_URL` + `QUERY_API_SECRET`.
  `select 1 as x` -> 200/x=1; `drop table foo` -> 400; `select * from
  nonexistent_xyz` -> 422; unauthenticated request -> 401. Same PASS/FAIL
  style, same jq-or-python3 json parsing.

## Checks performed

- `bash -n` on all three shell scripts: clean.
- Cross-checked every endpoint, status code, cookie format, env var name/
  default, and limit against `docs/CONTRACTS.md` (binding) — no code existed
  yet for `api/chat.js`, `web/index.html`, or `vercel.json` at write time
  (other Wave 1 tasks in flight), so this was written against the contract
  only, per instructions.
- Confirmed `scripts/deploy_vm.sh` (already present from Task 3) matches the
  quick-start reference in the README.

## Concerns

- `test/e2e.sh` step 4 budgets a 65s curl timeout against the contract's own
  "≤3 tool calls / 60s budget" — tight but should clear if the backend
  behaves; if it's flaky in practice, bump the curl `-m` a bit rather than
  the contract's 60s.
- The e2e test's real-data assertion (`tables[0].row_count>=1`) depends on
  there being at least one lead signup in the last 7 days in whatever
  environment it's run against; this is inherent to the spec, not a script
  bug, but worth knowing before treating a FAIL here as a code regression.
- `set_vercel_env.sh` assumes the `vercel` CLI is already logged into the
  correct Vercel project/scope (this repo's `.vercel/` project link) — it
  does not run `vercel link` itself, since that's an interactive/consent
  step outside this task's scope.
- Did not git commit, deploy, or ssh, per instructions.
