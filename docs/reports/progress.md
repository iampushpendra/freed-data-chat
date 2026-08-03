# SDD ledger — plan: docs/CONTRACTS.md + docs/DESIGN.md (freed-data-chat, parallel-wave mode per user directive)

Wave 1 (parallel implementers, disjoint files, no commits):
- Task 1: context/schema.md (KB distill + live introspection) — dispatched
- Task 2: vm/query_api.py + validator tests — dispatched
- Task 3: vm provisioning (systemd/nginx/setup/deploy_vm) — dispatched
- Task 4: web/index.html — dispatched
- Task 5: api/*.js + vercel.json + package.json — dispatched
- Task 6: README + e2e + set_vercel_env — dispatched

Wave 1: committed fdfcc13.
Wave 2 reviews done. Findings: 1 Critical (chat empty reply on backend-down), Important: validator set_config/lo_* gaps + 401 not audited; timeout budget >60s; CSV formula injection; setup.sh secret re-echo; login.js no try/catch. No XSS, no core validator bypass, auth solid.
Wave 3 fix wave (parallel, disjoint files) dispatched from BASE fdfcc13: query_api validator, chat.js/vercel.json, web csv, setup.sh/nginx/deploy.
Wave 4 (controller): commit fixes, provision VM, vercel env+deploy, E2E.
