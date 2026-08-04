# FREED Power BI Model — Relationships & Calculated-Column Reference

Distilled from the FREED Power BI semantic model (tmdl export) for use by an
LLM chatbot querying the warehouse `freed_app_db`. Power BI table names are
kebab-case display names (e.g. `client-file`); the underlying warehouse
tables are the real `schema.table` names documented in `context/schema.md`.
Date-dimension relationships (every `X.someDate → LocalDateTable_*.Date`) are
omitted as noise — only the ~29 real entity relationships are listed below.

---

## 1. Entity-relationship summary

All warehouse joins below use the same key semantics as the live Postgres
schema (see schema.md's "join-key pitfalls" section — this model encodes the
exact same client→client_file hop).

```
helixmongo.lead.leadRefId ── spinereadmongo.client.leadRefId          (1:*, active in PBI: client.leadRefId → lead-vital.leadRefId is the enforced path; lead↔lead_vital direct link is INACTIVE)
helixmongo.lead_vital.leadRefId ── spinereadmongo.client.leadRefId    (via "client.leadRefId → lead-vital.leadRefId", active)
spinereadmongo.client._id ── spinereadmongo.client_file_progress.client_oid   (active; PBI: client-file-progress.client_oid → client._id)
spinereadmongo.client_file_progress._id ── spinereadmongo.client_file.clientFileProgress_oid  (active, bothDirections)
  -- duplicate/legacy variant: client_file.clientFileProgress_oid → 'client-file-progress (2)'._id (INACTIVE — a second copy of client_file_progress exists in the model, see §2)
spinereadmongo.client_file._id ── spinereadmongo.fi_creditor.clientFile_oid   (active, bothDirections, one:*)
  -- NOTE: PBI's fi-creditor table is NOT a 1:1 mirror of spinereadmongo.fi_creditor; it's a pre-aggregated derived table (see §2/§3)
spinereadmongo.s_t_plan._id ── spinereadmongo.s_t_entry.stPlan_oid    (active, one:*)
spinereadmongo.client_file._id ── spinereadmongo.s_t_plan.clientFile_oid   (active, one:*)
spinereadmongo.client_file._id ── spinereadmongo.payment_entry.clientFile_oid  (active, one:*)
spinereadmongo.client_file._id ── spinereadmongo.payment_schedule_plan.clientFile_oid  (active, one:*)
spinereadmongo.payment_request._id ── spinereadmongo.payment_entry.paymentReq_oid  (active, one:*)
spinereadmongo.client_file.clientFileNumber ── spinereadmongo.client_file_consolidation.clientFileNumber  (active, one:*)
'client-file-progress (2)'.clientFileNumber ── payment-entry.clientFileNumber  (INACTIVE, bothDirections, *:*  — a redundant/legacy join, prefer the client_oid-based path)
helixmongo.lead_journey._id ── helixmongo.lead_trail.leadJourney_oid  (active, one:*)
helixmongo.lead.leadRefId ── helixmongo.lead_trail.leadRefId          (INACTIVE — lead_trail's primary active path is via lead_journey, not lead directly)
helixmongo.lead_vital.leadRefId ── helixmongo.lead_journey.leadRefId  (active, one:*)
helixmongo.lead_vital.leadRefId ── helixmongo.lead_subscription.leadRefId  (active, one:*)
helixmongo.lead_subscription._id ── helixmongo.lead_payment.refSubscription_oid  (active, one:*)
helixmongo.lead_vital.leadRefId ── 'client-file-progress (2)'.client_oid  (active — note: despite the name "client_oid", the PBI model actually joins this to leadRefId here, an odd/legacy mapping — treat with caution)
dcpreadmongo.dcp_financial_info.clientFile_oid ── dcpreadmongo.dcp_client_file._id  (active, one:*)
dcpreadmongo.dcp_client_file.client_oid ── spinereadmongo.client._id  (active, one:*)
'ClientFact_Lender'.clientFile_oid ── 'LenderFact'.clientFile_oid     (active, one:*; both are Power-BI-only fact tables, see §2)
dcpreadmongo.dcp_client_file._id ── 'ClientFact_Lender'.clientFile_oid  (active, bothDirections, one:*)
```

**Power-BI-only "hub" relationships** (fan into calculated tables that have
no single warehouse equivalent — flagged, not translated):

- `lead-trail.leadRefId ── 'Lead Dimension'.leadRefId` (active) — `Lead Dimension` is a *calculated* table (`SUMMARIZE('lead-trail', leadRefId, ...)`), not a warehouse table. It exists purely to carry a few RELATED attribution columns (`CI_Source`, `programProfile`, `Source 1`, `User Journey`) back onto `lead`/`client-file`/`dcp-client-file`.
- `lead-subscription.leadRefId ── 'Lead Dimension'.leadRefId` (bothDirections, active)
- `client-file.client_oid ── 'Lead Dimension'.leadRefId` (active)
- `'client-file-progress (2)'.client_oid ── 'Lead Dimension'.leadRefId` (active)
- `dcp-client-file.client_oid ── 'Lead Dimension'.leadRefId` (active)
- `client-file.client_oid ── Apptrove_Dim.leadRefId` (active) — `Apptrove_Dim` is a *calculated* `DISTINCT` table over `Apptrove_Signups` (a mobile-attribution import table, no warehouse source — see §2/Unmapped).
- `lead-subscription.leadRefId ── Apptrove_Dim.leadRefId` (active)

**Cardinality/direction caveats worth carrying into SQL:**
- The PBI model treats `client.leadRefId → lead_vital.leadRefId` as the *only active* lead↔lead_vital-family path; a direct `lead.leadRefId → lead_vital.leadRefId` relationship exists but is marked `isActive: false`. This mirrors schema.md's real-world advice to always hop `client_file → client → lead`, not `client_file → lead` directly.
- `fi-creditor.clientFile_oid → client-file._id` is `bothDirections` in the model (cross-filters both ways) — in raw SQL this is still a plain one-to-many join from `client_file` to `fi_creditor`.

---

## 2. Power BI table → warehouse table map

| PBI table | Warehouse schema.table | Notes |
|---|---|---|
| `lead` | `helixmongo.lead` (+ join) | Custom SQL: joins `lead` to `lead_vital` and an aggregate over `lead_journey` (PWA vs CI journey timing) — not a plain mirror. |
| `lead-vital` | `helixmongo.lead_vital` (+ join) | `SELECT DISTINCT ON (leadRefId)` deduped, LEFT JOIN `lead_journey` for `templateCode`. |
| `lead-vital (2)` | `helixmongo.lead_vital` | Second, simpler import of the same source table (subset of columns) — legacy duplicate, prefer `lead-vital`. |
| `lead-journey` | `helixmongo.lead_journey` | Straight column-subset mirror. |
| `lead-trail` | `helixmongo.lead_trail` | Straight mirror via native query (all columns). |
| `lead-subscription` | `helixmongo.lead_subscription` | Straight mirror. |
| `lead-payment` | `helixmongo.lead_payment` | Straight mirror. |
| `client` | `spinereadmongo.client` | Straight mirror. |
| `client-file` | `spinereadmongo.client_file` | Straight mirror (column subset). |
| `client-file-progress` | `spinereadmongo.client_file_progress` (+ `client_file`) | Native query joins in `client_file.totalCurrentOutstanding` / `assignedSalesRep`. |
| `client-file-progress (2)` | `spinereadmongo.client_file_progress` | Second, simpler import (no join) — legacy duplicate of `client-file-progress`; the model keeps both, with a mostly-inactive relationship set hanging off this copy. |
| `fi-creditor` | `spinereadmongo.fi_creditor` (aggregated) | **Not a row-level mirror.** Custom SQL aggregates `fi_creditor` per `clientFile_oid`: `MAX(daysDelinquentAsOnOnboarding)` → `max_days_delinquent`, `SUM(isSettled='true')` → `settled_accts`, then buckets delinquency into `Delinquent Groups`, and attaches the client's latest `clientFileNumber`. |
| `payment-entry` | `spinereadmongo.payment_entry` | Straight mirror (column subset). |
| `payment-request` | `spinereadmongo.payment_request` | Straight mirror. |
| `payment-schedule-plan` | `spinereadmongo.payment_schedule_plan` | Straight mirror. |
| `s-t-plan` | `spinereadmongo.s_t_plan` | Straight mirror. |
| `s-t-entry` | `spinereadmongo.s_t_entry` | Straight mirror. |
| `e-mandate` | `spinereadmongo.e_mandate` | Straight mirror. |
| `spinereadmongo client_file_consolidation` | `spinereadmongo.client_file_consolidation` | Straight mirror (table literally keeps the schema-qualified name as its PBI display name). |
| `dcp-client-file` | `dcpreadmongo.dcp_client_file` | Straight mirror. |
| `dcp-financial-info` | `dcpreadmongo.dcp_financial_info` | Straight mirror; booleans converted from text `'true'/'false'` to 1/0 (confirms schema.md's text-boolean note). |
| `dcp-fi-budget` | `dcpreadmongo.dcp_fi_budget` | Straight mirror (large household-budget column set). |
| `dcpreadmongo dcp_cl_creditor` | `dcpreadmongo.dcp_cl_creditor` | Straight mirror. |
| `lender-qualification-status` | `dcpreadmongo.lender_qualification_status` | Mirror + JSON-expand of `qualificationFieldList`. |
| `ameyo-call-details` | `insightreadmongo.ameyo_call_details` | Straight mirror (column subset) + bucketed columns (Credit Score/Debt/Salary buckets, not in schema.md — call-time snapshot fields). |
| `ClientFact_Lender` | *(no direct table)* | Import table of per-client lender-qualification field statuses (`CREDIT_SCORE_status/value`, `OUTGOING_FOIR_*`, `TOTAL_INCOME_*`, `MAX_ACCOUNTS_*`) — looks derived from `lender-qualification-status`'s expanded JSON fields, but source query wasn't a plain schema mirror; treat as PBI-only unless corroborated. |
| `LenderFact` | *(no direct table)* | Companion fact table to `ClientFact_Lender`, keyed by `clientFile_oid`; same caveat. |
| `Lead Dimension` | *(calculated table)* | `SUMMARIZE('lead-trail', leadRefId, ...)` — PBI-only convenience dimension, ultimately rooted in `helixmongo.lead_trail`. |
| `Apptrove_Signups` | *(external/no warehouse table)* | Mobile-attribution import (Apptrove is a 3rd-party attribution SDK) — `cuid`/`camp`/`ets`/`eid` columns, not present in `freed_app_db`. |
| `Apptrove_Dim` | *(calculated table)* | `DISTINCT` over `Apptrove_Signups` — PBI-only. |
| `Calendar` | *(none)* | Standard `CALENDAR()` date table, 2021-01-01 → today. |
| `Window Days` | *(none)* | `GENERATESERIES(0, 90, 1)` helper table for rolling-window measures. |
| `Measures (2)`, `Metrics`, `gap`, `CI Automation`, `DAX_ON_boarding` | *(none)* | Empty/measure-holder tables (`Row("Column", BLANK())` placeholders) — pure DAX-measure containers, no data. |
| `Cohort Size Table` | *(none, calculated)* | Cohort-sizing helper built from `lead-subscription`. |
| `Churn Master Table` | *(none, calculated)* | `ADDCOLUMNS(SUMMARIZE(FILTER('lead-subscription', [Is Churned]=1), ...))` — churn rollup derived from `lead-subscription`. |
| `Test_Events` / `SPA_Balance_Distribution` | *(none)* | `DATATABLE(...)` literal test/demo tables — not live data. |

---

## 3. Notable calculated columns

| Column | Table | Meaning | Warehouse-SQL equivalent |
|---|---|---|---|
| `Client Type` | client-file | "Multiple Files" if this client (`client_oid`) has more than one `clientFileNumber`, else "Single File". | `COUNT(DISTINCT "clientFileNumber") OVER (PARTITION BY "client_oid") > 1` on `spinereadmongo.client_file`. |
| `Client Stages` | client-file-progress | Human-readable funnel step ordering for `clientFileStage` (SIGN_UP→CREDIT_REPORT→BUDGET_DONE→PROGRAM_GENERATED→AGREEMENT_SIGNED→FEE_PAID→BANK_DETAILS_ENTERED→EMANDATE_DONE), numerically prefixed for sort order. | `CASE cfp."clientFileStage" WHEN 'SIGN_UP' THEN '1. SIGN_UP' ... END` on `spinereadmongo.client_file_progress`. |
| `dropStage` | client-file-progress | Coalesces `salesRepAssignedStage` over `clientFileStage` when the former is blank — "where in the funnel did this file actually drop." | `COALESCE(cfp."salesRepAssignedStage", cfp."clientFileStage")`. |
| `IsSameDayMandate` | client-file-progress | "Yes" if the file's `createdDate` equals its `firstEMandateActivatedDate` (mandate activated same day as sign-up). | `(cfp."createdDate" = cfp."firstEMandateActivatedDate")`. |
| `Org` | client-file / client-file-progress / dcp-client-file | Maps `orgCode` "INH" → "Inhouse", else "Affiliate" — sales-channel classification used everywhere `orgCode` appears. | `CASE WHEN "orgCode" = 'INH' THEN 'Inhouse' ELSE 'Affiliate' END`. |
| `Apptrove_Campaign` | client-file | Looks up the client's lead (`client.leadRefId` via `client_oid`) then joins to `Apptrove_Signups` on `cuid` to pull the mobile-install campaign (`camp`). PBI-only — no warehouse equivalent since `Apptrove_Signups` isn't in `freed_app_db`. | n/a |
| `FinalStatus` | e-mandate | Collapses raw `status` into "Success" (`ACTIVE`), "In-Process" (`DRAFTED`/`PENDING`), else "Failed". | `CASE WHEN "status"='ACTIVE' THEN 'Success' WHEN "status" IN ('DRAFTED','PENDING') THEN 'In-Process' ELSE 'Failed' END` on `spinereadmongo.e_mandate`. |
| `FinalAuthMode` | e-mandate | "UPI" if `isUPIMandate`, else maps `authMode` codes A/D/N → Aadhaar/Debit_Card/Net_Banking, else "Unknown". | `CASE WHEN "isUPIMandate"='true' THEN 'UPI' WHEN "authMode"='A' THEN 'Aadhaar' WHEN "authMode"='D' THEN 'Debit_Card' WHEN "authMode"='N' THEN 'Net_Banking' ELSE 'Unknown' END`. |
| `Activation Time (secs)` / `Update Time(sec)` | e-mandate | Seconds between `createdOn` and `activatedOn`/`lastUpdatedOn` — mandate onboarding latency. | `EXTRACT(EPOCH FROM ("activatedOn"::timestamptz - "createdOn"::timestamptz))`. |
| `Delinquent Groups`, `max_days_delinquent`, `settled_accts` | fi-creditor (PBI) | Per-client aggregate over the *real* `spinereadmongo.fi_creditor`: worst delinquency and count of settled creditors, bucketed into 5 delinquency bands (<30/30-60/60-90/90-120/120+ days). | See §2 — this is the full aggregate query already, reusable directly against `spinereadmongo.fi_creditor` grouped by `"clientFile_oid"`. |
| `Settled_Date` | s-t-plan | Simply aliases `sifUploadDate` — confirms schema.md's "canonical settled date" designation. | `sp."sifUploadDate"`. |
| `trustToCLient+Creditor` | s-t-entry | Sum of `amountFromTrustToClient` + `amountFromTrustToCreditor` — total trust outflow regardless of settlement case (1 vs 2). | `("amountFromTrustToClient"::numeric + "amountFromTrustToCreditor"::numeric)` on `spinereadmongo.s_t_entry`. |
| `leadSourceCode`, `programProfile` (various tables) | client, lead-journey, lead-subscription, lead-trail, client-file-progress | `RELATED('lead-vital'[...])` pass-throughs — confirms schema.md's note that `programProfile`/`leadSourceCode` live canonically on `lead_vital` and get denormalized onto related tables via the leadRefId relationship. | Join back to `helixmongo.lead_vital` on `leadRefId`. |
| `CreatedDate_*` (many tables) | client-file, client-file-progress, e-mandate, lead*, payment-entry, etc. | `INT(<date column>)` — Power BI's date-serial trick purely for building the inactive `Calendar` role-playing relationships; not a business metric. | n/a (modeling artifact only). |
| `'Year-Month(pending cancelled)'` | client_file_consolidation | `FORMAT(pendingCancelledAt, "YYYY-MM (MMM)")` — month bucket for cancellation reporting. | `to_char(("pendingCancelledAt")::timestamptz, 'YYYY-MM (Mon)')`. |

---

## Unmapped / Power-BI-only

- **`Calendar`, `Window Days`** — pure date/number-sequence helper tables, no warehouse source.
- **`Measures (2)`, `Metrics`, `gap`, `CI Automation`, `DAX_ON_boarding`** — empty measure-holder tables (no rows, DAX measures only).
- **`Test_Events`, `SPA_Balance_Distribution`** — literal `DATATABLE()` demo/test data, not live.
- **`Cohort Size Table`, `Churn Master Table`** — calculated tables derived entirely inside the model from `lead-subscription`; no 1:1 warehouse table (could be reconstructed with SQL window functions over `helixmongo.lead_subscription` if needed, but nothing to map directly).
- **`Lead Dimension`** — calculated `SUMMARIZE` over `lead-trail`; a modeling convenience, not a warehouse table.
- **`Apptrove_Signups`, `Apptrove_Dim`** — third-party mobile-attribution data (Apptrove), not present in `freed_app_db` at all.
- **`ClientFact_Lender`, `LenderFact`** — per-client/lender qualification-field snapshots; likely built from `dcpreadmongo.lender_qualification_status`'s JSON expansion but the tmdl doesn't show a plain mirroring query, so treat as unverified/PBI-only rather than mapped.
- **All `LocalDateTable_*` / `DateTableTemplate_*` tables and every relationship targeting them** — Power BI's auto-generated per-column date hierarchies (excluded from this document per instructions; there are ~140 such relationships, one per date/datetime column in the model).
- **Inactive relationships** worth remembering as "don't use directly": `lead.leadRefId → lead-vital.leadRefId`, `lead-trail.leadRefId → lead.leadRefId`, `client-file.clientFileProgress_oid → 'client-file-progress (2)'._id`, `'client-file-progress (2)'.clientFileNumber → payment-entry.clientFileNumber`. In all four cases the model's *active* path is a different, more indirect route (documented in §1) — mirrors the live-schema guidance to hop through `client` rather than joining directly.
