# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this project uses [Semantic Versioning](https://semver.org/).

## Database / schema-awareness note

This app has no migrations of its own — it's a read-only consumer of the
`freed_app_db` warehouse (Metabase db 27). For this project, the
schema-awareness knowledge base the LLM relies on (`context/schema.md`:
distilled table/column schema + business-metric SQL recipes) is treated as
the versioned "schema" surface: any change to it that alters how a metric is
computed, or reflects a real warehouse schema change upstream, gets its own
CHANGELOG entry under a `### Database` sub-heading, even when no app code
changes — same as ADR-003 requires for literal DB migrations.

## [1.0.0] - 2026-08-06

### Repository synchronization — 2026-11-01

- Document the existing application and establish its changelog.
- Excluded local credentials, runtime caches, and customer exports from this synchronization.


Initial build and hardening, deployed live at `freed-data-chat.vercel.app`.

### Added
- Password-gated chat UI (`web/index.html`) with markdown replies, result
  tables, CSV download, and a collapsible SQL disclosure panel.
- `api/login.js` / `api/chat.js`: signed-cookie auth + OpenAI tool-calling
  loop (`run_sql` tool, up to 3 calls / 60s per message).
- `vm/query_api.py`: stdlib-only Python query API on the VM — SELECT/WITH-only
  validator, read-only Postgres role, row cap (2,000), statement timeout
  (60s), per-request JSON-line audit log.
- Interim Cloudflare quick-tunnel bridge so Vercel can reach the VM's query
  API without an inbound port (`vm/tunnel_setup.sh` scaffolds the upgrade to
  a stable named tunnel).
- "Analyst" rebrand: superhero hero theme + inline SVG emblem.
- Full Power BI wiki ingested into `context/schema.md`: DAX→SQL metric
  recipes, model relationship map, Apptrove notes, company/business rules.
- Eval harness (`evals/golden.yaml` + `evals/run_evals.py` +
  `evals/read_log.py`): golden metric questions graded in
  `--canonical-only` (regression) or `--full` (bot-vs-canonical) mode.

### Changed
- VM-fetch timeout raised 15s -> 40s (canonical signup query took ~22s and
  was being aborted).

### Fixed
- SQL validator bypass via `E''`-style string literals.
- Chat timeout handling now distinguishes a slow query from a real outage,
  and retries transient VM blips once.
- CSV export injection risk in downloadable result tables.
- A secret leak fixed prior to making the repo shareable.
- Reliability/accuracy pass: inject current date into the system prompt,
  ban false "no data available" coverage claims, pin time-window
  conventions (e.g. "last week" = previous Mon-Sun), temperature 0.

### Database
- Corrected the working definition of "signup" used in generated SQL: was
  effectively counting `lead` creation timestamp; canonical definition is
  the `lead_trail` milestone `SIGN_UP COMPLETED`, distinct by `leadRefId`.
  No warehouse schema changed — this was a knowledge-base (`context/schema.md`)
  correction, logged here per the DB-schema-changelog convention.
- Identified but not yet applied: `lead_trail` has no index on
  `(milestone, status, startTime)`, causing full scans (~20s+) on
  signup-style queries. Tracked as a follow-up; the app's read-only DB role
  cannot apply it itself.

### Security
- Documented HTTP-not-HTTPS caveat on the Vercel -> VM leg (tunnel encrypts
  in transit at the Cloudflare hop, but the origin leg is plain HTTP); the
  bearer secret is the accepted single point of failure until upgraded.
- Repo scrubbed of real infra values (VM IP, password, tunnel URL) before
  treating it as shareable/public-readable.
