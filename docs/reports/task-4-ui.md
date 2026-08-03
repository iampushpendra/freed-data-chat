# Task 4 — Web UI

**Status:** Complete

**Deliverable:** `/Users/pushpendrasingh/projects/freed-data-chat/web/index.html` — single self-contained file (inline CSS + JS, no external requests, no CDN fonts, all icons as inline SVG).

## What was built

- Title "FREED Data Chat", teal (#0f766e family) accent, white/dark-aware cards, subtle shadows and rounded corners, system font stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, ...`). House aesthetic referenced from `agent-dashboard/index.html` (header layout, card/border/shadow conventions, light+dark CSS variable pattern) without copying its code.
- **Login view:** centered card, password input + button, `POST /api/login {password}`. On `{ok:true}` (200) switches to chat. On any non-ok response, the card shakes (CSS keyframe) and shows an inline error. Page loads optimistically into the chat view; the first `/api/chat` 401 flips back to the login view.
- **Chat view:** header with product name + "New chat" button (clears in-memory history), scrollable message list, composer with an auto-growing textarea (Enter = send, Shift+Enter = newline, disabled while a request is pending), and an animated three-dot typing indicator shown while waiting on `/api/chat`.
- **Send flow:** `POST /api/chat {messages: [...last 12 {role, content}]}`. Reply markdown is escaped for HTML first, then a hand-rolled renderer adds support for `**bold**`, `` `code` ``, fenced code blocks, `- ` lists, `## ` headings, GFM-style tables, and line breaks. No raw server text is ever put into `innerHTML` unescaped — plain text nodes are used for user bubbles and table cell values; only the sanitized markdown output goes through `innerHTML`.
- **Tables:** each entry in `tables` renders as its own scrollable card: row-count badge, `truncated` badge when set, "Download CSV" button (builds CSV client-side from the full `columns`/`rows` with RFC-4180 style quoting, filename `freed-data-<ISO-timestamp>.csv`), a horizontally+vertically scrollable table capped at 200 displayed rows with a "showing 200 of N" note when more rows exist, and a collapsed `<details>` disclosure holding the raw SQL.
- **Errors:** non-200 `/api/chat` responses (other than 401, which triggers the login flip) render as a red-tinted inline system message with a retry hint; `fetch` rejection (network failure) renders the same style of message.
- **Empty state:** icon + heading + 3 example question chips ("How many leads signed up last week?", "Settlement amount collected this month", "Lead funnel by source for July") that fill the composer textarea on click and focus it.
- **Responsive:** composer is pinned to the bottom of the flex column (with `env(safe-area-inset-bottom)` padding), tables scroll horizontally inside their own container, bubble/card max-widths tighten under a 640px breakpoint.

## How it was verified

- Extracted the `<script>` body to `/private/tmp/.../scratchpad/extracted.js` and ran `node --check` — passed, no syntax errors.
- Copied out the pure markdown-rendering functions (`escapeHtml`, `renderInline`, `isTableSepLine`, `splitTableRow`, `renderMarkdown`) into a standalone Node test script and ran representative inputs: an XSS payload (`<script>alert(1)</script>` mixed with `**bold**`/`` `code` ``), a heading + list + paragraph mix, a markdown table, and a fenced code block. All escaped correctly and produced the expected HTML structure (no unescaped tags reached the output).
- `grep`-checked the file for any `http://`/`https://`/`cdn.`/`fonts.googleapis` references outside of the inline `xmlns="http://www.w3.org/2000/svg"` SVG namespace attributes — none found, confirming zero external requests.
- Manually traced the login-flip logic (optimistic chat view on load, `/api/chat` 401 → `showLogin()`) and the composer keyboard handling (Enter vs Shift+Enter) against the contract text.
- Did not attempt a live browser render: the file lives outside the currently-attached preview project, so the browser tool could only load it as a static (non-interactive) snapshot. No JS runtime/DOM behavior was exercised in-browser — only statically verified as above.

## Concerns

- No live DOM/browser test was possible in this environment (files outside the attached project render as static snapshots only, not interactive), so real click/keyboard interaction and cross-browser rendering are unverified beyond static/Node-level checks. Recommend a quick manual open in an actual browser before/after wiring up `/api/login` and `/api/chat` on Vercel.
- `web/index.html` currently has no live backend to talk to yet, so the login/chat flows have only been verified against the contract's shapes (`{ok:true}` / `{reply, tables}` / 401 handling), not against a running `api/login.js` / `api/chat.js`.
- Per instructions, this file was not committed or deployed.

## Review fix — CSV formula injection (2026-08-04)

Two reviewers independently flagged that `csvQuote` (previously lines 696-702) only quoted cells containing `" , \n \r` and did not neutralize spreadsheet formula injection: a DB value such as `=1+1`, `+cmd`, `-2`, or `@SUM(...)` (e.g. from a lead's name/description field) would be written unescaped and could execute as a formula when the exported CSV is opened in Excel/Sheets.

**Fix:** in `csvQuote`, before the existing RFC-4180 quote/escape logic, added an OWASP-style guard — if the cell's string value starts with `=`, `+`, `-`, `@`, tab, or CR, prefix it with a leading apostrophe (`'`) so spreadsheet apps treat it as inert text instead of a formula. Applies to every cell value and the header row (both go through `csvQuote`). The existing comma/quote/newline wrapping and `""`-doubling behavior is unchanged and still runs after the new guard.

**Verification:** extracted the `<script>` body to a temp file in the scratchpad and ran `node --check` — passed. Also copied the standalone `csvQuote` function into a scratch script and confirmed: `csvQuote("=1+1")` → `'=1+1`, `csvQuote("+cmd")` → `'+cmd`, `csvQuote("-2")` → `'-2`, `csvQuote("@SUM(1)")` → `'@SUM(1)`, while `csvQuote("normal, value")` still correctly quotes as `"normal, value"` and `csvQuote("plain")` is untouched.

Only `web/index.html` was edited; no commit or deploy was made.
