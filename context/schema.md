# FREED Data Warehouse — Schema & Business Context

FREED is a debt-relief company operating in India (Debt Resolution / Debt
Consolidation programs). This document is the schema + glossary backbone for
writing PostgreSQL against `freed_app_db` — a read-only Postgres warehouse
(same data Metabase "db 27" reads) that mirrors several MongoDB services via
Airbyte CDC. You only have a read-only role; treat every answer as
best-effort analytics, not a system of record.

**Critical dialect facts:**
- All tables live in non-`public` schemas — always schema-qualify (e.g.
  `helixmongo.lead`, `spinereadmongo.client_file`).
- Columns are camelCase and MUST be double-quoted (`l."leadRefId"`), or
  Postgres folds them to lowercase and the query fails with "column does not
  exist". Table names themselves are already snake_case and unquoted.
- Because this is Mongo-via-Airbyte, almost every business column — dates
  included — is `character varying`, not a native `timestamp`. Date/time
  columns are ISO-8601 strings like `'2026-08-03T18:41:51.058Z'` (UTC).
  String-compare works for range filters (`>= '2026-01-01'`), but for
  arithmetic/formatting cast explicitly: `("createdAt")::timestamptz`.
- Boolean-ish columns (`isArchived`, `isSettled`, `isFirstEMandateDone`, …)
  are also stored as **text** `'true'`/`'false'`/`NULL`, not real booleans.
  Filter with `= 'true'`/`= 'false'` or `IS NULL`, not `= TRUE`.
- Every Airbyte-mirrored table carries CDC bookkeeping columns you should
  never SELECT: `_airbyte_raw_id`, `_airbyte_extracted_at`,
  `_airbyte_meta`, `_airbyte_generation_id`, `_ab_cdc_cursor`,
  `_ab_cdc_deleted_at`, `_ab_cdc_updated_at`. Filter out soft-deletes with
  `"_ab_cdc_deleted_at" IS NULL` when correctness matters (e.g. financial
  sums).
- Some very large tables have known **fan-out** risk on naive joins (one
  lead → many client_file rows, etc.) — always check
  `COUNT(*) vs COUNT(DISTINCT key)` before trusting a joined row count, and
  prefer aggregating before joining wide.

---

## Tables reference

Row counts are `pg_class.reltuples` estimates (order of magnitude, not
exact) — treat anything over ~1M rows as "always filter/aggregate/LIMIT".

### Lead funnel — schema `helixmongo`

**`lead`** (~2.1M rows) — one row per lead (top of funnel, pre-client).
- `_id`, `leadRefId` (business key used everywhere else), `dedupeId`
- `firstName`, `lastName`, `status` (`NEW`, `UNVERIFIED`)
- `createdAt`, `createdOn`, `firstTouchAt` (ISO strings)
- `source_oid`, `address_oid`, `authUser_oid`, `personalDetails_oid` (FKs)
- `syncedWithSpine` — whether this lead converted into `spinereadmongo.client`

**`lead_vital`** (~2.25M) — credit/financial snapshot per lead, 1 row/lead
(latest values, not history).
- `leadRefId`, `creditScore`, `monthlyIncome`, `monthlyObligation`,
  `foirPercentage` (FOIR = fixed obligation to income ratio), `emiMissed`
- `leadSourceCode`, `programProfile`, `programProfileRule` — the program the
  lead is scored into (DRP/DCP/CI/DEP segment logic)
- `lastCreditPullDate` — legacy credit-pull marker (see glossary: Credit
  Pull has a cutover on 2025-11-17; prefer `lead_trail` events after that)

**`lead_source`** (~2.1M) — acquisition source per lead. `leadRefId`,
`code`, `name`, `sourceType`, `createdAt`.

**`lead_activity`** (event log) — `leadRefId`, `activityType`, `status`,
`description`, `createdAt`.

**`lead_journey`** — templated journey/milestone tracking. `leadRefId`,
`journeyName`, `templateCode`, `currentMileStone`, `status`, `startTime`,
`endTime`, `payload` (jsonb).

**`lead_trail`** (~20.5M, event log) — every automated/agent milestone event
per lead: `leadRefId`, `milestone`, `status`, `startTime`. **Canonical
source for "Credit Pull" events since 2025-11-17**: filter
`"milestone" IN ('CREDIT_PULL_SINGLE_USE','CREDIT_PULL') AND "status" = 'COMPLETED'`.

**`lead_program_details`** (~1.6M) — enrollment outcome per lead:
`leadRefId`, `programCode`, `clientFileId`, `clientFileNumber`,
`leadProgramStatus`, `enrolledAt`, `archived`, `archiveReason`.

**`lead_address`**, **`contact`**, **`utm_params`** — 1-2 rows per lead:
address, phone/email contact points (`contactType`, `contactValue`,
`isVerified`), and marketing attribution (`utmSource`, `utmMedium`,
`utmCampaign`, `fbclid`).

**`auth_user`**, **`o_t_p_session`**, **`login_logs`** — app auth/session
tables, `leadRefId`-keyed.

### Client / settlement — schema `spinereadmongo`

**`client`** (~1.4M) — a lead that converted into an onboarded client.
`_id`, `leadRefId` (FK back to `helixmongo.lead`), `mobile`, `emailId`,
`fullName`, `programCode`, `linkedClientFileId`.

**`client_file` (aka "cf")** (~1.7M) — one row per client-program
enrollment file (a client can have multiple, e.g. one per program attempt).
Key columns: `_id`, `client_oid` (**FK to `client._id`, NOT to
`lead.leadRefId`** — see join-key warning below), `clientFileNumber`,
`clientFileStatus` (`LEAD`, `AGREEMENT_SIGNED`, `ACTIVE`, `HOLD`,
`GRADUATED`, `CANCELLED`, `PENDING_CANCELLED`, `REJECTED`,
`PARTIALLY_GRADUATED`, `AGREEMENT_SIGNED_REVOKED`), `programCode` (`DRP`
1.03M / `DCP` 0.67M), `disposition`, `subDisposition`, `dropOffReason`,
`creditScore`, `totalCurrentOutstanding`, `spaSavingAmount`,
`isFirstEMandateDone`, `defaultEMandate_oid`, `saleHappenedDate`,
`assignedRM`/`assignedSalesRep`, `clientFileProgress_oid` (FK to
`client_file_progress`), `currentPSPlan_oid`.

**`client_file_progress`** (~1.77M) — lifecycle-stage timestamps/flags for
a `client_file` (very wide, ~90 cols). Notable: `isEnrolled`,
`isAgreementSigned`, `isRejected`, `isRetained`, `isCancelled`,
`isTerminated`, `isFirstEMandateDone`, `firstEMandateActivatedAt/Date` (=
**AOM**, see glossary), `enrolledAt/Date`, `cancelledAt/Date`,
`graduatedAt/Date`, `onboardingFeePaidAt/Date`, `settledCreditorCount`,
`activeCreditorCount`, `spaPaidCount`, `msfPaidCount`,
`currentPFFPlan_oid`, `clientFileStage`, `clientFileNumber`.

**`client_file_consolidation`** — per-client money-flow rollup (small,
join by `clientFileNumber`). `spaBalance` (canonical current SPA/trust
balance), `totalIncomingAmountToTrust`, `totalOutgoingAmountFromTrust`,
`collectedByKFS` (total fee landed at Freed — verify case-by-case, not
always pristine), `refundedToClient`, `settledToCreditor`.

**`s_t_plan`** ("s-t-plan", ~37K rows) — one row per (client × creditor)
settlement deal. `_id`, `clientFile_oid`, `fiCreditor_oid`, `draftDate`,
`isArchived` (text `'true'`/NULL — filter `IS DISTINCT FROM 'true'` for
qualifying rows), `settlementType` (`LUMPSUM`/`TERM`), `totalOutstanding`,
`settlementAmount`, `settlementFeeAmount` (fee due incl. GST),
`settlementFeePercentage`, `feeAmountReceivedFromTrust` (canonical
"service fee collected"), `feeAmountReceivedFromClient`,
`sifUploadDate` (canonical settled date), `isSIFVerified`, `isAnnulled`.

**`s_t_entry`** ("s-t-entry", ~55K rows) — one row per money-movement event
tied to a plan (many rows per plan). `stPlan_oid` (FK), `clientFile_oid`,
`fiCreditor_oid`, `draftDate`, `isArchived`,
`amountFromTrustToCreditor` (Case 1: platform settlement),
`amountFromTrustToClient` (Case 2: bank refused trust → refund),
`amountFromClientToCreditor` (Case 3: client paid directly).

**`s_t_fee_entry`** (~52K) — fee-specific ledger rows tied to a plan:
`stPlan_oid`, `amount`, `status`, `paymentApproach`, `isArchived`.

**`payment_entry`** (~1.45M) — the general ledger of money movements.
`_id`, `clientFile_oid`, `amount`, `utr`, `txnPurpose` (`SPA` 765K,
`ONBOARDING_FEE` 314K, `MSF` 281K legacy, `PFF` 87K, `SERVICE_FEE` 58K,
`SETTLEMENT` 10K, `SLEEPING_CHARGES`, `TBT`, `SSF`/`SSFReversal`),
`fromAccountEntity`/`toAccountEntity` (`CLIENT`, `TRUST`, `KFS`),
`paymentEntryStatus` (filter `= 'SETTLED'` for realized cash),
`settledAt`/`settledDate`, `presentedAt`, `paymentReferenceNumber`.

**`fi_creditor`** ("fi-creditor", ~3.0M) — **included-only** creditor rows
Freed is actively working (settlement/DRP side; keyed by `clientFile_oid`).
`isSettled` (text; `'true'` 10.8K / `'false'` 3.09M / NULL 4.8K),
`settledBy`, `daysPastDue`, `creditorBalance`, `creditorAmountOverdue`,
`settlementAmount`, `settledAmount`, `settlementDate`, `emiAmount`,
`creditorStatus`, `debtType`, `clCreditor_oid` (FK to `cl_creditor`).

**`cl_creditor`** ("cl-creditor", ~7.9M) — **included + excluded** full
creditor footprint per client (higher row count than `fi_creditor` by
design — don't treat them as interchangeable). `client_oid`,
`clientFile_oid`, `creditorBalance`, `creditorAmountOverdue`, `debtType`,
`daysPastDue`, `originalSanctionedAmount`.

**`e_mandate`** (~306K) — NACH/UPI e-mandate records. `clientFile_oid`,
`status` (filter `= 'ACTIVE'` for live mandates), `isUPIMandate`,
`amount`, `authMode`, `provider`, `createdOn`, `activatedOn`,
`isDefault`, `whoDidIt` (agent who created/switched it),
`firstCollectionDate`. Rank per client with
`ROW_NUMBER() OVER (PARTITION BY "clientFile_oid" ORDER BY "createdOn")`
to find first vs. switched mandates.

**`fi_document`** (~908K) — documents (SIF etc.) tied to `fiCreditor_oid`/
`clientFile_oid`: `documentType`, `isVerifed`, `verifiedAt`, `signedOn`.

**`fi_summary`** (~904K) — per-client rollup: `clientFile_oid`,
`spaAmount`, `msfAmount`, `totalOutstanding`, `totalSaving`,
`numberOfCreditorsTotal`, `numberOfCreditorsSettled`, `remainingTenure`.

### DCP-side creditor tables — schema `dcpreadmongo`

**`dcp_cl_creditor`** (~4.1M) — DCP program's own creditor rows (older,
in-model variant — separate namespace from the DRP-side `cl_creditor`
above; confirm which one a metric should point at). `client_oid`,
`currentBalance`, `closureAmount`, `emi`, `roi`, `sanctionedLimit`.

**`partner_lender_info`** (~1.0M) — partner-NBFC loan info for DCP
consolidation, source of the ~2.5-3% lender commission. `clientFile_oid`,
`topUpAmount`, `roi`, `tenure`, `isApprovedForBankSubmission`,
`isLenderPolicyMatched`.

### Calls — schema `insightreadmongo`

**`ameyo_call_details`** (~17.8M rows, dialer call log — always filter by
date/campaign, never scan unfiltered). `callSid`, `callDate`,
`callDateTime`, `callType`, `callStatus`, `callAgentId`, `campaignId`,
`callPhoneNo`, `depositionCode`/`depositionClass` (call outcome),
`systemDeposition`, `callDurationSeconds`, `dialerVendor`.

### Other large tables worth knowing (schema-qualify, avoid unfiltered scans)

`ppmongo.network_request_log` (~132M), `helixmongo.creditor` — no, that's
`helixmongo.creditor` reference master, `helixmongo.integration_api_log`
(~25.4M), `spine_event_store."SpineStream_event_entry"` (~28.3M, needs
double-quoted mixed-case table name), `helixmongo.lead_data_generation_log`
(~3.6M), `helixmongo.lead_data_for_capi` (~1.3M) — these are technical/
audit logs, rarely relevant to business questions; only touch with a tight
date filter and LIMIT.

### Known join-key pitfalls (verified against live data)

- `spinereadmongo.client_file."client_oid"` points to
  `spinereadmongo.client."_id"`, **not** `helixmongo.lead."leadRefId"`
  directly. To connect a `client_file` back to its originating lead, hop
  through `client`: `client_file.client_oid = client._id` then
  `client.leadRefId = lead.leadRefId`. A direct
  `client_file.client_oid = lead.leadRefId` join silently returns near-all
  NULLs.
- Joining `lead` → `client_file` (directly or via `client`) fans out:
  one lead can have multiple `client_file` rows (multiple program
  attempts). Always check
  `COUNT(*) vs COUNT(DISTINCT "leadRefId")` before trusting a joined count;
  de-duplicate (e.g. `DISTINCT ON` or pick latest `client_file` per lead)
  when the question is about leads, not client-files.
- `programCode` lives on both `client_file` and `lead_vital`/
  `lead_program_details` — for pre-conversion segment questions use
  `lead_vital.programProfile`; for post-conversion program use
  `client_file.programCode`.

---

## Business glossary

**SPA (Special Purpose Account)** — trust account where clients deposit a
fixed monthly instalment. Settlements are funded from here first.
Balance: `client_file_consolidation.spaBalance`. Gross inflow:
`SUM(payment_entry.amount)` where `txnPurpose='SPA'`,
`fromAccountEntity='CLIENT'`, `toAccountEntity='TRUST'`,
`paymentEntryStatus='SETTLED'`.

**Platform Settlement (Case 1)** — SPA balance fully covered the
settlement; trust paid the creditor directly. Signature:
`SUM(s_t_entry."amountFromTrustToCreditor") > 0` for the plan.

**Direct Settlement — Bank Rejected Trust (Case 2)** — bank refused
payment from trust; trust refunded SPA to client, client paid creditor
themselves; fee still deducted from SPA. Signature:
`SUM(s_t_entry."amountFromTrustToClient") > 0` for the plan (can be split
across multiple refund rows).

**Direct Settlement — Insufficient SPA (Case 3)** — SPA didn't cover the
settlement; client paid creditor directly, trust only took its fee.
Signature: `SUM(s_t_entry."amountFromClientToCreditor") > 0 AND
SUM(s_t_entry."amountFromTrustToClient") = 0`.
Universal qualifier for all three cases: `"isArchived" IS DISTINCT FROM 'true'`
on both `s_t_plan` and `s_t_entry`.

**Service Fee** — 15% of `s_t_plan."totalOutstanding"`, plus 18% GST on
that fee (≈17.7% of totalOutstanding total). Recorded as
`s_t_plan."settlementFeeAmount"` (fee due); collected amount is
`s_t_plan."feeAmountReceivedFromTrust"` (canonical — do not sum
`payment_entry` for this, known data-quality issue there).

**PFF (Platform Fee)** — 10% of the client's monthly SPA, charged every
month while in DRP (even if that month's SPA wasn't paid — deducted from
existing balance instead). At the client's final settlement, accumulated
PFF offsets that account's Service Fee (don't naively
`SUM(PFF)+SUM(ServiceFee)` at program level — double-counts).
Query: `SUM(payment_entry.amount)` where `txnPurpose='PFF'`,
`fromAccountEntity='TRUST'`, `toAccountEntity='KFS'`,
`paymentEntryStatus='SETTLED'`.

**MSF (Monthly Service Fee)** — legacy/discontinued platform fee, `
txnPurpose='MSF'` in `payment_entry`. Historical only — don't use for
current revenue.

**Onboarding Fee** (aka Evaluation Fee, ~₹1,500) — one-time fee at DRP
enrollment, before SPA setup. `txnPurpose='ONBOARDING_FEE'` in
`payment_entry` (confirmed live: 314K rows).

**KFS** — Freed's own revenue account; `toAccountEntity='KFS'` in
`payment_entry` means money arrived at Freed.

**AOM (Account On-boarding via Mandate)** — client activated their first
e-mandate; treated as start-of-active-relationship date. Column:
`client_file_progress."firstEMandateActivatedDate"` (also on
`client_file."isFirstEMandateDone"`/`"defaultEMandate_oid"`).

**Funnel stages — Prevention / Care / Cure** — Freed's product taxonomy:
Prevention = Credit Insights subscription (non-delinquent, credit-health
curious); Care = Debt Consolidation Program (DCP), stressed but repaying;
Cure = Debt Resolution Program (DRP), delinquent/under collection
pressure. Maps loosely to `client_file.programCode` / `lead_vital.programProfile`
values (`DRP`, `DCP`, and other program codes for CI/DEP).

**Settled Creditor** — `fi_creditor."isSettled" = 'true'` (text, not
boolean). fi_creditor is the *included-only* creditor set; use
`cl_creditor` for the client's full (included+excluded) creditor
footprint.

**Credit Pull** — since 2025-11-17, canonical source is `lead_trail`
events (`milestone IN ('CREDIT_PULL_SINGLE_USE','CREDIT_PULL') AND
status='COMPLETED'`); before that cutover, only `lead_vital.lastCreditPullDate`
snapshot exists (most-recent-pull-only, not a full event history).

**HOT/WARM/COLD lead temperature** — an existing tag/scoring layer lives
outside `freed_app_db` in a separate call-analysis pipeline; it is not one
of the tables documented above. If a question needs it, say so rather than
inventing a computation from this warehouse's columns.

---

## Example Q → SQL

**Q: How many leads signed up in the last 7 days?**
```sql
SELECT COUNT(*) AS lead_count
FROM helixmongo.lead
WHERE ("createdAt")::timestamptz >= now() - interval '7 days'
LIMIT 1;
```

**Q: What's the credit-pull volume by month for 2026 (post-cutover logic)?**
```sql
SELECT
    date_trunc('month', (lt."startTime")::timestamptz)::date AS month,
    COUNT(*) AS credit_pulls
FROM helixmongo.lead_trail AS lt
WHERE lt."milestone" IN ('CREDIT_PULL_SINGLE_USE', 'CREDIT_PULL')
  AND lt."status" = 'COMPLETED'
  AND lt."startTime" >= '2026-01-01'
GROUP BY 1
ORDER BY 1
LIMIT 100;
```

**Q: How many active clients does each program (DRP vs DCP) have?**
```sql
SELECT cf."programCode", COUNT(*) AS active_files
FROM spinereadmongo.client_file AS cf
WHERE cf."clientFileStatus" = 'ACTIVE'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 50;
```

**Q: What share of settlement plans are Platform (Case 1) vs Direct
(Case 2/3)?**
```sql
WITH plan_case AS (
    SELECT
        sp."_id" AS plan_id,
        CASE
            WHEN COALESCE(SUM(se."amountFromTrustToCreditor"), 0) > 0 THEN 'Platform (Case 1)'
            WHEN COALESCE(SUM(se."amountFromTrustToClient"), 0) > 0 THEN 'Direct - Bank Rejected (Case 2)'
            WHEN COALESCE(SUM(se."amountFromClientToCreditor"), 0) > 0 THEN 'Direct - Insufficient SPA (Case 3)'
            ELSE 'Unclassified'
        END AS settlement_case
    FROM spinereadmongo.s_t_plan AS sp
    LEFT JOIN spinereadmongo.s_t_entry AS se
        ON se."stPlan_oid" = sp."_id" AND se."isArchived" IS DISTINCT FROM 'true'
    WHERE sp."isArchived" IS DISTINCT FROM 'true'
    GROUP BY sp."_id"
)
SELECT settlement_case, COUNT(*) AS plans,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM plan_case
GROUP BY settlement_case
ORDER BY plans DESC
LIMIT 10;
```

**Q: Total PFF collected in the last quarter?**
```sql
SELECT SUM(pe."amount") AS total_pff
FROM spinereadmongo.payment_entry AS pe
WHERE pe."txnPurpose" = 'PFF'
  AND pe."fromAccountEntity" = 'TRUST'
  AND pe."toAccountEntity" = 'KFS'
  AND pe."paymentEntryStatus" = 'SETTLED'
  AND (pe."settledAt")::timestamptz >= date_trunc('quarter', now()) - interval '3 months'
  AND (pe."settledAt")::timestamptz <  date_trunc('quarter', now())
LIMIT 1;
```

**Q: How many leads convert to a client_file, de-duplicated (avoiding
fan-out)?**
```sql
SELECT
    COUNT(*) AS total_lead_clientfile_rows,
    COUNT(DISTINCT l."leadRefId") AS distinct_leads
FROM helixmongo.lead AS l
JOIN spinereadmongo.client AS c ON c."leadRefId" = l."leadRefId"
JOIN spinereadmongo.client_file AS cf ON cf."client_oid" = c."_id"
WHERE l."createdAt" >= '2026-01-01'
LIMIT 1;
```

---

## Rules

- PostgreSQL dialect only. Always double-quote camelCase columns.
- Every query must end in `LIMIT` (default 200 unless the question implies
  otherwise) — the query API also hard-caps at 2,000 rows server-side.
- Never `SELECT *`; select only the columns needed.
- Prefer aggregates (`COUNT`, `SUM`, `GROUP BY`) over raw row dumps for
  anything touching a table over ~1M rows.
- Timestamps are ISO-8601 UTC strings on almost every table; cast with
  `::timestamptz` before date-math, and remember "now" in the warehouse is
  UTC, not IST — mention the timezone if the user asks for "today"/"this
  week" in business terms.
- Boolean-like columns are text (`'true'`/`'false'`/NULL) — filter with
  string equality or `IS [NOT] NULL`, never `= TRUE`.
- NULL-safe filters: use `IS DISTINCT FROM`, `COALESCE`, or explicit
  `IS NULL`/`IS NOT NULL` rather than assuming `<>`/`=` handle NULLs.
- If a requested concept isn't in this document (e.g. HOT/WARM/COLD lead
  temperature), say so rather than guessing a column/table.
- On a 422 (Postgres error), read the error message and self-correct once
  before giving up.
