# Ingest report: Power BI DAX → PostgreSQL metrics.md

**Output**: `/Users/pushpendrasingh/projects/freed-data-chat/context/_ingest/metrics.md`
(18,136 chars, 25 metric entries + mapping-notes section).

## Environment constraint (important)

This pass had **no network route to the live VM**: `ssh -i
<ssh-key-path> ubuntu@<vm-host>` timed out from this
sandbox (confirmed with verbose SSH — TCP connect timeout, not an auth
failure). No `psql`/`information_schema` verification against the live
warehouse was possible this session. All mappings instead lean on
`context/schema.md` (the already-verified warehouse doc) as ground truth,
and every metric is labeled Verified / Approximated / Unmapped based on
whether it could be built purely from names `schema.md` already confirms
live. Someone with VM access should re-run the `information_schema` +
`LIMIT 5` spot-checks listed at the end of this report before the
Approximated/Unmapped entries are trusted in production.

## Sources read

- `FREED_NewDAX.md` (full — curated measure log, richest source)
- `funnel_day_on_day.dax`, `Apptrove_Dim_flags.dax`
- `tables/Measures (2).tmdl`, `Metrics.tmdl`, `DAX_ON_boarding.tmdl` (full),
  `Churn Master Table.tmdl` (full), `Cohort Size Table.tmdl`,
  `SPA_Balance_Distribution.tmdl`, `lead.tmdl` (full), `e-mandate.tmdl`
  (partial), `lead-subscription.tmdl` (partial), `s-t-plan.tmdl`/
  `s-t-entry.tmdl` (grepped for calculated-column definitions),
  `Apptrove_Signups.tmdl` (grepped), `apptrove_events_all3.pq` (head)
- `context/schema.md` (full, read first as instructed)

## Translated (25 metric entries)

**Verified** (10): Signup Count, Credit Pull Count, Signup→Credit-Pull %,
Service Fee (net), Platform Fee/PFF (net), Onboarding Fee (net), MSF
(legacy), e-Mandate Success Rate (ACTIVE leg), AOM Count, Settlement Case
Split (Platform vs Direct — reused schema.md's own worked example verbatim).

**Approximated** (11, directional/best-effort — DAX depended on Power-BI-only
calculated columns absent from the warehouse): Net Clients/Cancelled
Clients %, Cancelled-after-AOM %, Total Net Revenue rollup, Settled Debt %,
SPA Balance Distribution (bucket edges guessed), No. of SPA Paid
(Full)/Full SPA 1+/2+, Total SPA Paid (Direct), Settled Count/%, DCP-SQL
Qualification, DCP UnsecuredBalance/Debt Bucket, DRP-/DCP-Eligible Credit
Pull %.

**Unmapped** (4, explicitly flagged, no query given or query marked
speculative): Stage Drop Rate (needs `Drop Count App`/`Drop Stage`, not in
schema.md's ~90 documented `client_file_progress` columns); Cohort Size /
Subscription Initialized-Activated-Cancelled / Cumulative Cohort Churn %
(all three depend on `lead-subscription`/`lead-payment`, tables **not
present anywhere** in `context/schema.md` — the CI-subscription product
isn't covered by that doc at all); Apptrove Signup/Registration/Subscription
flags (confirmed from `apptrove_events_all3.pq` to be a live external API
call to `api.apptrove.com`, never replicated into `freed_app_db`); HOT/WARM/
COLD lead temperature (schema.md's own glossary already says this lives
outside the warehouse).

## Items needing live re-verification (highest priority first)

1. Does `helixmongo.lead_subscription` (and `lead_payment`) exist, and with
   what columns? Blocks 3 of the 25 metrics (cohort/churn/CI-subscription
   funnel).
2. Does `lead_vital.programProfile` hold literal values `'DRP_Eligible'` /
   `'DCP_Eligible'`? Used by the DRP/DCP-eligible credit-pull % approximation.
3. `e_mandate.status` value set beyond the confirmed `'ACTIVE'` — are
   `'DRAFTED'`/`'PENDING'` real values?
4. Does `dcpreadmongo.dcp_client_file` exist with `inSrcSalary`,
   `dateCreated`, `employmentType`, `creditScore` columns? Blocks the DCP-SQL
   qualification metric.
5. Does `dcpreadmongo.dcp_cl_creditor.accountType` exist, and is
   `'UNSECURED'` a real value? Blocks UnsecuredBalance/Debt Bucket.
6. Real `SPA Balance Bucket` cut points (currently guessed at ≤5K/25K/50K/
   1L/2.5L bands) for the SPA Balance Distribution metric.

## Status

Complete given the environment's network limitation. `context/schema.md`
was read first and used as the authoritative name source per instructions;
no other files were modified; no commit made.
