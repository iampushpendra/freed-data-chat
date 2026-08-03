# Review — Task 5 (Vercel serverless backend)

Reviewed: `api/chat.js`, `api/login.js`, `api/_lib/auth.js`, `package.json`, `vercel.json`, `.vercelignore`, `.gitignore` against `docs/CONTRACTS.md` ("Vercel serverless", "vercel.json").

Verified empirically, not just by inspection:
- Ran `node --input-type=module` scripts importing `api/_lib/auth.js` to unit-test `signCookie`/`verifyCookie`/`timingSafeEqual`/`parseCookie`: sign→verify round-trip ✅, tampered HMAC ✅ false, wrong secret ✅ false, expired ✅ false, missing cookie ✅ false, multi-cookie header parsing ✅ extracts `fdc_auth` correctly, HMAC/expiry buffer-length mismatch ✅ returns `false` with no throw, malformed exp / no-dot value ✅ false.
- Ran `npx vercel build` against the actual linked Vercel project to inspect the real output bundle (`.vercel/output/functions/api/chat.func/`). **Cleaned up afterward** — deleted `.vercel/output`, `.vercel/.env.preview.local` (downloaded real preview env vars/secrets) and `.vercel/node` since this review had no need to retain them locally.

## Verdict: **Changes requested** (1 Critical, 2 Important, 3 Minor)

## Spec compliance

| Item | Status |
|---|---|
| Cookie format `<exp>.<hmac>`, HttpOnly/Path=/ /Max-Age=604800/SameSite=Lax/Secure | ✅ |
| Login 200 `{ok:true}` / 401 `{ok:false}` | ✅ |
| Chat 401 on missing/invalid/expired cookie | ✅ |
| Body shape `{messages:[{role,content}]}`, trimmed to 12 | ✅ |
| Tool loop: `run_sql`, max 3 executions | ✅ |
| Tables collection (full rows) vs. model view (100-row truncation) | ✅ (verified at chat.js:236 vs. 238) |
| 405 on non-POST (both endpoints) | ✅ |
| `context/schema.md` bundled into the Lambda despite `process.cwd()`-relative read | ✅ — confirmed via real `vercel build`: `.vc-config.json` shows `handler: api/chat.js`, and `context/schema.md` is physically present under `chat.func/context/schema.md`. `@vercel/nft`'s static analysis resolved the `fs.readFileSync(path.join(process.cwd(),'context','schema.md'))` call and traced it correctly; `.vercelignore` does not exclude `context/`. No `functions.includeFiles` needed — but see Important #2 below for why this is fragile. |
| No secrets logged or returned to client | ✅ — grepped all three files, zero `console.*` calls; JSON responses never include env values. |
| Backend-unreachable reply says "data backend unavailable" | ❌ — see Critical below |

## Critical

**chat.js:259-267 — backend-unreachable path returns an empty `reply`, not an explanation.**
When `runSql()` fails (fetch throw, or non-200/422 status), `result.backendDown` is set true and pushed as a tool message. The outer loop then does `if (backendDown) break;` **immediately**, without calling OpenAI again. The model never gets a turn to compose the "data backend unavailable" explanation the contract requires — `lastAssistantContent` is left at whatever it was when the tool call was *issued* (an OpenAI message with `tool_calls` set has `content: null`, so `lastAssistantContent` stays `''`). Net effect: `res.status(200).json({reply: '', tables: []})` — the UI would render a blank assistant bubble instead of an explanation.
Fix: when `backendDown` is set, don't break before one more `callOpenAI` round (or hardcode a fallback reply like `"The data backend is currently unavailable — please try again shortly."` if `lastAssistantContent` is still empty at the end).

## Important

1. **login.js has no try/catch around the handler body.** Contract focus item 1 asks about 401/405/500 paths; login.js only produces a 500 for the explicit misconfigured-env-var check (line 12-15). Any other unexpected exception (e.g. `createHmac` throwing on a malformed secret type) propagates uncaught, and Vercel's Node runtime returns its own generic HTML/JSON error shape instead of the app's `{error: "..."}` convention that `chat.js` uses everywhere else. Low probability, but inconsistent error contract between the two endpoints. Wrap the body in try/catch mirroring `chat.js`.

2. **`fs.readFileSync(path.join(process.cwd(), ...))` for schema loading is correct today but fragile, and undocumented in `vercel.json`.** It works only because `@vercel/nft`'s tracer happened to statically resolve this particular call shape in the currently-installed CLI version (confirmed by an actual `vercel build`). This is not guaranteed by Vercel's docs, which explicitly recommend `functions.includeFiles` (or `__dirname`-based paths) for anything read via `fs` at runtime rather than `import`/`require`, precisely because dynamic/`cwd()`-based reads are not part of the documented contract for the tracer. Recommend adding `"functions": {"api/chat.js": {"includeFiles": "context/**"}}` to `vercel.json` to pin this behavior — otherwise a future Vercel/nft version bump could silently reintroduce the FALLBACK_SCHEMA stub in production with no error, no log, and no page for anyone to notice (the report itself flags this as a real risk in its "Concerns" section, and it should be closed out rather than left to chance).

## Minor

1. **`package.json` engines `"node": "20.x"` — Vercel CLI reports this is deprecated** ("Deployments created on or after 2026-10-01 will fail to build. Please set `engines: {node: '24.x'}`"). Today is 2026-08-04, so this is ~2 months from becoming a hard deploy failure. Contract text says "Node 20" but was presumably written before this deprecation was announced — worth a follow-up ticket, not blocking this review.
2. **Empty/malformed OpenAI response** (`data.choices` missing or empty) silently produces `{reply: '', tables: []}` with a 200 rather than a clearer error — same failure mode as the Critical above, just a different trigger. Low priority since it requires OpenAI itself to return a malformed payload.
3. `timingSafeEqual` (auth.js:18-23) returns `false` immediately on length mismatch before the constant-time compare, leaking password/HMAC length via timing. Already called out by the implementer's report as an accepted trade-off for a shared-password internal tool — flagging only for completeness, no action needed.

## Not a defect (verified, ruled out)

- `req.body` string-vs-object handling in both `login.js`/`chat.js`: not a double-parse bug. Vercel pre-parses JSON bodies when `Content-Type: application/json` is set, but leaves `req.body` as a string/Buffer if the header is absent/wrong — the `typeof body === 'string'` guard correctly handles that edge case without double-parsing.
- Malformed `tool_calls` / missing `call.function` / bad JSON in `call.function.arguments`: all guarded (`?.` + try/catch at chat.js:220-225).
- `export default handler(req,res)` + `"type":"module"`: correct shape for Vercel Node 20 ESM functions, confirmed by the successful `vercel build` (both `chat.func` and `login.func` built cleanly).
