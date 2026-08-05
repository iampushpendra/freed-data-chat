# Ingest report: apptrove.md

**Output:** `/Users/pushpendrasingh/projects/freed-data-chat/context/_ingest/apptrove.md` (11,401 chars)

## What was extracted

1. **Apptrove events reference** — Apptrove is a third-party mobile
   attribution API (`api.apptrove.com`), pulled directly into Power BI via
   `Web.Contents`/import mode — confirmed **not** mirrored into
   `freed_app_db` (audit tags it `Source Type = Web API`, `Schema = (API)`).
   Documented: the 3 tracked events (Signup/Registration/Subscription and
   their opaque `eid`s), the row shape (`cuid`, `camp`, `ets`, etc.), that
   `cuid` = Freed's `leadRefId` (the lead join key), and that the
   `apptrove_events_*.pq` variants are three ways of calling the paginated
   API robustly — not dedup logic. The real "one row per lead" collapsing
   happens in the `Apptrove_Dim` DAX (`SUMMARIZE` on `cuid` + per-event
   `1/0` flags + first-touch campaign attribution by `MIN(ets)`). Flagged
   explicitly that Apptrove has no credit-pull event (that's
   `helixmongo.lead_trail` in the warehouse, per `schema.md`) and that
   there's no direct SQL query for "Apptrove signups" since the data isn't
   in Postgres — gave a fallback example joining a known `leadRefId` back
   into `helixmongo.lead`/`spinereadmongo.client_file` instead. Noted
   `Test_Events` is an unrelated manually-curated release-milestone
   annotation table, not Apptrove data.

2. **Columns actually in use** — distilled from
   `FREED_PowerBI_Column_Usage_Audit.xlsx` ("Used Columns (keep)" sheet,
   187 of 661 total model columns), cross-referenced against
   `FREED_DB_Columns_InUse.xlsx` for schema-qualified names. Per-table
   bullet lists for `lead`, `lead_vital`, `lead_trail`, `lead_journey`,
   `lead_payment`, `lead_subscription`, `client`, `client_file`,
   `client_file_progress` (both model roles), `client_file_consolidation`,
   `e_mandate`, `payment_entry`, `s_t_plan`, `s_t_entry`, `fi_creditor`,
   `payment_request`, `payment_schedule_plan`, the `dcpreadmongo` tables,
   and `ameyo_call_details`. Relationship/join-key columns bolded (e.g.
   `client_file.clientFileNumber` at 36 refs, `lead_trail.leadRefId` at 30
   refs — the two most-used columns in the model). Added a coverage
   caveat: the audit only sees DAX/relationship usage, not report-visual-only
   usage, so it's a floor, not an exhaustive whitelist.

## Files parsed

- `apptrove_events_all3.pq`, `apptrove_events_loop_per_eid.pq`,
  `apptrove_events_multi_eid.pq` — parsed (plain text/M code).
- `tables/Apptrove_Dim.tmdl`, `tables/Apptrove_Signups.tmdl`,
  `tables/Test_Events.tmdl` — parsed.
- `Apptrove_Dim_flags.dax` — parsed (had the key dedup/flag logic and the
  explicit "no credit-pull event in Apptrove" note).
- `FREED_PowerBI_Column_Usage_Audit.xlsx` — parsed via openpyxl
  (`Used Columns (keep)` + `READ ME` sheets).
- `FREED_DB_Columns_InUse.xlsx` — parsed via openpyxl (`Summary` +
  `Columns In Use` sheets), used to confirm schema-qualified table names.
- `FREED_Columns_In_Use.xlsx`, `FREED_DB_Tables_Columns.xlsx` — opened
  successfully (sheet names/shapes confirmed) but not deep-dived; content
  overlapped with the two files above and the 6-12k char budget was
  already met without them.
- `context/schema.md` — read first to align terminology/join-key warnings.
- CHAT_*.md / FREED_BusinessContext.md — grepped for "apptrove", no hits.
- Live DB check (SSH to `<vm-host>`) — attempted but **failed**: SSH
  timed out at the network level (host unreachable from this environment,
  likely needs VPN/bastion access). Proceeded without it; the .pq/.tmdl/.dax
  evidence (`Source Type = Web API`) was already conclusive that Apptrove
  isn't in the warehouse, so this doesn't weaken the doc's core claim.
