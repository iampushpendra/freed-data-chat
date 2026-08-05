# Capture wiring: question + chat_id on the Analyst query store

## Summary

Added optional natural-language question capture to the Analyst query
audit log: `api/chat.js` now sends `question` (the current user turn,
capped to 500 chars) and `chat_id` (a `crypto.randomUUID()` correlation id)
alongside `sql` on every `/query` call; `vm/query_api.py` accepts both as
optional, logging-only body fields (never touching SQL validation or
execution) and records them in the JSON audit log via a new pure helper,
`build_audit_record()`, capped to 1000/64 chars respectively and explicitly
`null` when absent. Added `evals/read_log.py`, a stdlib-only convenience
reader that prints a compact newest-last table of logged runs (or the
scp/ssh pull command if no local copy is given). Documented the new fields
in `docs/CONTRACTS.md`.

## Files changed

- `api/chat.js` — `runSqlOnce`/`runSql` now take `question`/`chatId` and
  include them in the POST body; handler computes `chatId` once per
  request and extracts+trims the latest user message as `currentQuestion`.
  No change to response handling, timeouts, retry logic, or the tool loop.
- `vm/query_api.py` — module docstring's endpoint section documents the
  optional fields; new `QUESTION_MAX_LEN`/`CHAT_ID_MAX_LEN` constants, new
  pure `build_audit_record(base, question=None, chat_id=None)` helper
  (caps + null-fill, does not mutate `base`); `do_POST` reads
  `question`/`chat_id` from the parsed body (non-strings silently dropped,
  never rejected) and threads them through the validate/execute/success
  audit calls. SQL validator and security checks untouched.
- `vm/test_query_api.py` — new `TestBuildAuditRecord` class (6 tests):
  presence, null-when-omitted, no-mutation of base, base-field
  preservation, question truncation, chat_id truncation.
- `evals/read_log.py` — new, stdlib-only. `--file` to read a local log
  copy; otherwise prints the scp/ssh instructions to pull
  `/home/ubuntu/freed-data-chat/chat-query.log` from the VM. Supports
  `--limit N` and `--errors-only`; prints newest-last.
- `docs/CONTRACTS.md` — one line added under the VM query API section
  documenting `question`/`chat_id` as optional logging-only metadata with
  their caps.

## Verification

- `node --check api/chat.js` → passed.
- `python3 vm/test_query_api.py` → **96 tests, all passed** (90 pre-existing
  + 6 new `TestBuildAuditRecord` tests).
- Manually smoke-tested `evals/read_log.py` against a fabricated local
  JSON-lines file: table output, `--errors-only` filter, and the
  no-`--file` instructions path all behaved as expected.

## Not done here (by design / instruction)

- No git commit.
- No ssh/deploy. **The VM change requires redeploying `query_api.py` to the
  VM (`scripts/deploy_vm.sh`) before the new fields actually get logged.**
  Until that redeploy, the VM's running process ignores the unknown
  `question`/`chat_id` fields sent by the already-updated `chat.js` —
  degrades gracefully, no error, just not logged yet.
