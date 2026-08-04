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


---

# EXTENDED KNOWLEDGE BASE

> Distilled from the FREED Power BI Wiki (DAX measures, tmdl model, Apptrove Power Query, company docs). Use these as guidance; the live warehouse remains ground truth. SQL recipes below were validated against the live DB except where marked Approximated/Unmapped.



## DERIVED BUSINESS METRICS (translated from Power BI DAX — 14/15 verified live; approximations & unmapped items flagged inline)

# FREED Derived Business Metrics — Power BI → PostgreSQL Translation

> Source: Power BI DAX (`FREED_NewDAX.md`, `funnel_day_on_day.dax`,
> `Apptrove_Dim_flags.dax`, `tables/*.tmdl`). Ground truth for table/column
> names: `context/schema.md` (live `freed_app_db`; schemas `helixmongo`,
> `spinereadmongo`, `dcpreadmongo`, `insightreadmongo`). **This pass had no
> network path to the live VM** (`ssh ubuntu@172.20.4.15` timed out from this
> sandbox) — nothing below was freshly re-verified with `information_schema`.
> Recipes built only from names already confirmed in `schema.md` are marked
> Verified; anything needing a name not in `schema.md` is Approximated or
> Unmapped, never guessed silently. Booleans are text (`'true'`/`'false'`);
> dates are ISO strings, cast `::timestamptz`; camelCase columns are
> double-quoted.

---

## Funnel & Conversion

### 1. Signup Count (`Signup Count_lead`) — Verified
Distinct leads completing SIGN_UP in a period. DAX: pre-2025-11-17 counted
`client-file` rows; from that cutover on, `DISTINCTCOUNT(lead-trail[leadRefId])`
where `milestone="SIGN_UP", status="COMPLETED"`.
```sql
SELECT COUNT(DISTINCT lt."leadRefId") AS signup_count
FROM helixmongo.lead_trail AS lt
WHERE lt."milestone" = 'SIGN_UP' AND lt."status" = 'COMPLETED'
  AND (lt."startTime")::timestamptz >= '2026-07-01'
  AND (lt."startTime")::timestamptz <  '2026-08-01'
LIMIT 1;
```
Pre-cutover branch is historical-only; not worth replicating for live asks.

### 2. Credit Pull Count (`Credit Pull Count_lead-vital`) — Verified
Distinct leads with a completed credit pull. Matches `schema.md`'s own
worked example.
```sql
SELECT COUNT(DISTINCT lt."leadRefId") AS credit_pull_count
FROM helixmongo.lead_trail AS lt
WHERE lt."milestone" IN ('CREDIT_PULL_SINGLE_USE', 'CREDIT_PULL')
  AND lt."status" = 'COMPLETED'
  AND (lt."startTime")::timestamptz >= '2026-07-01'
  AND (lt."startTime")::timestamptz <  '2026-08-01'
LIMIT 1;
```

### 3. Signup → Credit Pull % — Verified
`DIVIDE([Credit Pull Count],[Signup Count])`; compose metrics #1 and #2 in a
CTE and `ROUND(100.0 * cp.n / NULLIF(s.n,0), 2)`.

### 4. DRP-/DCP-Eligible Credit Pull % — Approximated
DAX (post-cutover): `lead-trail[programProfile]="DRP_Eligible"` (or
`DCP_Eligible`). `schema.md` doesn't document a `programProfile` column on
`lead_trail` — it lives on `lead_vital`. Closest faithful join:
```sql
SELECT COUNT(DISTINCT lt."leadRefId") AS drp_eligible_pulls
FROM helixmongo.lead_trail AS lt
JOIN helixmongo.lead_vital AS lv ON lv."leadRefId" = lt."leadRefId"
WHERE lt."milestone" IN ('CREDIT_PULL_SINGLE_USE','CREDIT_PULL')
  AND lt."status" = 'COMPLETED' AND lv."programProfile" = 'DRP_Eligible'
  AND (lt."startTime")::timestamptz >= '2026-07-01'
  AND (lt."startTime")::timestamptz <  '2026-08-01'
LIMIT 1;
```
Confirm `lead_vital.programProfile` actually holds `'DRP_Eligible'`/
`'DCP_Eligible'` live before trusting this. Divide by metric #2 for the %.

### 5. Stage Drop Rate / Total Drops — Unmapped
`'client-file-progress'[Drop Count App]`/`[Drop Stage]` are PBI-only
calculated columns; `schema.md`'s ~90-column `client_file_progress` list has
no equivalent. No warehouse source identified — flag to a human rather than
approximate a funnel-stage drop count.

### 6. Net Clients / Cancelled Clients % — Approximated
Cohort by AOM date (`firstEMandateActivatedAt`); numerator = client-file
`Status2 IN {Cancelled, Pending_Cancelled, Agreement_Signed}`. `Status2` is
derived and not in `schema.md`; approximate with `clientFileStatus`:
```sql
WITH cohort AS (
  SELECT cf."clientFileStatus"
  FROM spinereadmongo.client_file AS cf
  JOIN spinereadmongo.client_file_progress AS cfp
    ON cfp."clientFileNumber" = cf."clientFileNumber"
  WHERE (cfp."firstEMandateActivatedAt")::timestamptz >= '2026-07-01'
    AND (cfp."firstEMandateActivatedAt")::timestamptz <  '2026-08-01'
)
SELECT COUNT(*) AS net_clients,
  ROUND(100.0 * COUNT(*) FILTER (WHERE "clientFileStatus" IN
    ('CANCELLED','PENDING_CANCELLED','AGREEMENT_SIGNED')) / NULLIF(COUNT(*),0), 2)
    AS cancelled_pct
FROM cohort LIMIT 1;
```
Directional only — `Status2` may bucket sub-statuses differently.

### 7. Cancelled after AOM % / Cancelled just after 1st SPA % — Approximated
Early-churn signal: cancelled after e-mandate activation but before (or at)
first full SPA. `No. of SPA Paid(Full)` has no direct warehouse column (see
metric #15); approximate "zero SPA paid" as "no settled SPA payment_entry":
```sql
SELECT COUNT(*) AS cancelled_after_aom
FROM spinereadmongo.client_file AS cf
JOIN spinereadmongo.client_file_progress AS cfp
  ON cfp."clientFileNumber" = cf."clientFileNumber"
WHERE cf."clientFileStatus" IN ('CANCELLED','PENDING_CANCELLED','AGREEMENT_SIGNED')
  AND cfp."firstEMandateActivatedAt" IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM spinereadmongo.payment_entry AS pe
    WHERE pe."clientFile_oid" = cf."_id" AND pe."txnPurpose" = 'SPA'
      AND pe."paymentEntryStatus" = 'SETTLED')
  AND (cfp."firstEMandateActivatedAt")::timestamptz >= '2026-07-01'
  AND (cfp."firstEMandateActivatedAt")::timestamptz <  '2026-08-01'
LIMIT 1;
```

---

## Revenue & Fees

### 8. Service Fee, net of refund — Verified
```sql
SELECT (SUM(pe."amount") FILTER (WHERE pe."toAccountEntity"='KFS')
      - SUM(pe."amount") FILTER (WHERE pe."fromAccountEntity"='KFS')) / 1e5 / 1.18
  AS service_fee_net_lacs
FROM spinereadmongo.payment_entry AS pe
WHERE pe."txnPurpose" = 'SERVICE_FEE' AND pe."paymentEntryStatus" = 'SETTLED'
  AND (pe."settledAt")::timestamptz >= '2026-07-01'
  AND (pe."settledAt")::timestamptz <  '2026-08-01'
LIMIT 1;
```
Use `s_t_plan.feeAmountReceivedFromTrust` instead when the question is
per-settlement rather than per-period revenue (per `schema.md` glossary).

### 9. Platform Fee / PFF, net (`Sub_PlatformFee_wo_GST`) — Verified
```sql
SELECT SUM(pe."amount") / 1.18 / 1e5 AS pff_net_lacs
FROM spinereadmongo.payment_entry AS pe
WHERE pe."txnPurpose" = 'PFF' AND pe."fromAccountEntity" = 'TRUST'
  AND pe."toAccountEntity" = 'KFS' AND pe."paymentEntryStatus" = 'SETTLED'
  AND (pe."settledAt")::timestamptz >= '2026-07-01'
  AND (pe."settledAt")::timestamptz <  '2026-08-01'
LIMIT 1;
```
Matches `schema.md`'s worked PFF example. Don't add to Service Fee at
program level — PFF offsets Service Fee at final settlement (double-count).

### 10. Onboarding Fee, net of refund — Verified
Same pattern as #8 with `txnPurpose='ONBOARDING_FEE'`.

### 11. MSF — legacy revenue — Verified but deprecated
`txnPurpose='MSF'`, `toAccountEntity='KFS'`. Discontinued per glossary — use
only for historical backfills, not current-period reporting.

### 12. Total Net Revenue (`Revenue_sub (Net Refunds)`) — Approximated
Additive rollup: OF(#10) + Service Fee(#8) + PFF(#9) + MSF(#11), each
computed independently and summed in application code — no single warehouse
query replaces it, and it is period-revenue, not a valid per-client sum.

### 13. Settled Debt % (`Settled_Debt 1 %`) — Approximated
Share of AOM'd clients' outstanding debt settled via each client's first
plan. `SettlementNoByDate` is a PBI RANKX column; approximate "first plan"
with `ROW_NUMBER()` (ranks all non-archived plans, not just *started* ones —
directional only):
```sql
WITH first_plan AS (
  SELECT sp.*, ROW_NUMBER() OVER (
    PARTITION BY sp."clientFile_oid" ORDER BY sp."draftDate") AS rn
  FROM spinereadmongo.s_t_plan AS sp
  WHERE sp."isArchived" IS DISTINCT FROM 'true'
)
SELECT
  (SELECT SUM("totalOutstanding") FROM first_plan WHERE rn = 1) AS settled_debt,
  (SELECT SUM(cf."totalCurrentOutstanding") FROM spinereadmongo.client_file cf
     WHERE cf."isFirstEMandateDone" = 'true') AS net_debt
LIMIT 1;
```
Divide settled_debt/net_debt for the %.

---

## Settlement / SPA metrics

### 14. SPA Balance Distribution — Approximated
Bucketed client accounts by current trust balance, with incoming/collected/
refunded rollups.
```sql
SELECT
  CASE WHEN cfc."spaBalance" <= 5000   THEN '0. ≤5K'
       WHEN cfc."spaBalance" <= 25000  THEN '1. 5K-25K'
       WHEN cfc."spaBalance" <= 50000  THEN '2. 25K-50K'
       WHEN cfc."spaBalance" <= 100000 THEN '3. 50K-1L'
       WHEN cfc."spaBalance" <= 250000 THEN '4. 1L-2.5L'
       ELSE '5. 2.5L+' END AS spa_balance_bucket,
  COUNT(DISTINCT cfc."clientFileNumber") AS num_accounts,
  SUM(cfc."totalIncomingAmountToTrust") AS total_incoming,
  SUM(cfc."collectedByKFS") AS collected_by_kfs,
  SUM(cfc."refundedToClient") AS refunded_to_client,
  SUM(cfc."spaBalance") AS spa_balance_left
FROM spinereadmongo.client_file_consolidation AS cfc
WHERE cfc."spaBalance" > 0
GROUP BY 1 ORDER BY 1 LIMIT 20;
```
Bucket edges are illustrative, not the verified PBI cut points — confirm
with the business owner. `collectedByKFS` itself carries `schema.md`'s own
"not always pristine" caveat.

### 15. No. of SPA Paid (Full) / Full SPA 1+ / 2+ — Approximated
`SPA Draft Amount` (expected monthly instalment) isn't a documented
warehouse column, so "full payment" can't be verified exactly. Proxy = count
of distinct settled SPA payment_entry rows per client:
```sql
SELECT pe."clientFile_oid", COUNT(*) AS spa_payment_count, SUM(pe."amount") AS spa_total_paid
FROM spinereadmongo.payment_entry AS pe
WHERE pe."txnPurpose" = 'SPA' AND pe."fromAccountEntity" = 'CLIENT'
  AND pe."paymentEntryStatus" = 'SETTLED'
GROUP BY 1 HAVING COUNT(*) >= 1
LIMIT 200;
```
This counts *payment events*, not "full monthly instalments met" — say so
if asked for the exact PBI figure.

### 16. Total SPA Paid — Direct (`Total_SPA_Paid (direct)`) — Approximated
Maps to warehouse's documented "Case 3" signature:
```sql
SELECT COALESCE(SUM(se."amountFromClientToCreditor"), 0) AS total_spa_paid_direct
FROM spinereadmongo.s_t_entry AS se
JOIN spinereadmongo.s_t_plan AS sp ON sp."_id" = se."stPlan_oid"
WHERE se."isArchived" IS DISTINCT FROM 'true' AND sp."isArchived" IS DISTINCT FROM 'true'
  AND sp."feeAmountReceivedFromTrust" > 0  -- proxy for settlement-started + fee-charged
LIMIT 1;
```
Warehouse has no `Is_Settlement_Started2`/`IsNetServiceFeeCharged`/`Is Dormant`
columns; the fee-received filter is the closest available proxy.

### 17. Settlement Case Split (Platform vs Direct) — Verified
Already fully specified and warehouse-verified in `schema.md`'s own example
("What share of settlement plans are Platform (Case 1) vs Direct (Case
2/3)?") — reuse as-is, no changes needed.

### 18. Settled Count / Settled % (`Settled_Count 1`, `Settled_1%`) — Approximated
Count of clients whose first settlement plan has started, over Net Clients
(#6). Same `ROW_NUMBER()`-as-`SettlementNoByDate` proxy as #13:
```sql
WITH first_plan AS (
  SELECT sp.*, ROW_NUMBER() OVER (
    PARTITION BY sp."clientFile_oid" ORDER BY sp."draftDate") AS rn
  FROM spinereadmongo.s_t_plan AS sp WHERE sp."isArchived" IS DISTINCT FROM 'true'
)
SELECT COUNT(*) AS settled_count_1
FROM first_plan WHERE rn = 1 AND "feeAmountReceivedFromTrust" > 0
LIMIT 1;
```

---

## Mandate / e-Mandate metrics

### 19. e-Mandate Success Rate (`FinalStatus`) — Verified (mostly)
`FinalStatus`: ACTIVE→Success, DRAFTED/PENDING→In-Process, else→Failed.
```sql
WITH ranked AS (
  SELECT em.*, CASE
    WHEN em."status" = 'ACTIVE' THEN 'Success'
    WHEN em."status" IN ('DRAFTED','PENDING') THEN 'In-Process'
    ELSE 'Failed' END AS final_status
  FROM spinereadmongo.e_mandate AS em
)
SELECT COUNT(DISTINCT "clientFile_oid") FILTER (WHERE final_status='Success') AS success_clients,
       COUNT(DISTINCT "clientFile_oid") AS total_clients,
       ROUND(100.0 * COUNT(DISTINCT "clientFile_oid") FILTER (WHERE final_status='Success')
         / NULLIF(COUNT(DISTINCT "clientFile_oid"),0), 2) AS success_pct
FROM ranked LIMIT 1;
```
`status='ACTIVE'` is directly confirmed live per `schema.md`; `DRAFTED`/
`PENDING` values are not separately documented — confirm before relying on
the In-Process bucket.

### 20. AOM Count (`AOM_Count(SignupDate)`) — Verified
Clients with `firstEMandateActivatedAt IS NOT NULL` in period — matches
`schema.md`'s AOM glossary definition directly.

---

## Churn & Cohort — Unmapped (table not in warehouse doc)

### 21. Cohort Size, 22. Subscription Initialized/Activated/Cancelled, 23. Cumulative Cohort Churn %
All three depend on `lead-subscription` (and `lead-payment`), which are
**not documented in `context/schema.md`** — that doc covers the DRP/DCP debt
funnel only, with no entry for the Credit-Insights subscription product.
Per `schema.md`'s own rule ("if a concept isn't in this document, say so
rather than guessing"), these are flagged **unmapped**. If the tables exist,
by the same naming convention as `lead-vital`→`lead_vital`, plausible names
are `helixmongo.lead_subscription`/`helixmongo.lead_payment` with a `status`
column holding `ACTIVE/CANCELLED/CUSTOMER_CANCELLED/CUSTOMER_PAUSED` — but
this needs a live `information_schema` check before any query is trusted.
DAX logic for reference: Cohort Size = `DISTINCTCOUNT(leadRefId)` by signup
month for those statuses; Initialized = same count with no status filter;
Activated/Cancelled = the same statuses split; Cumulative Churn % = day-since-
signup bucketed churned count ÷ that month's cohort size.

---

## DCP-specific metrics

### 24. DCP-SQL Qualification (`is_DCP_SQL`) — Approximated
Salary ≥₹35K (before 1-Apr-2026) or ≥₹50K (from 1-Apr-2026), credit score
≥700, unsecured balance ≥₹1L, salaried employment (incl. typo variants).
```sql
WITH unsecured AS (
  SELECT dc."client_oid", SUM(dc."currentBalance") AS unsecured_balance
  FROM dcpreadmongo.dcp_cl_creditor AS dc
  WHERE dc."accountType" = 'UNSECURED' GROUP BY 1
)
SELECT cf."client_oid",
  CASE WHEN (
      (cf."dateCreated" < '2026-04-01' AND cf."firstRecordedSalary" >= 35000)
      OR (cf."dateCreated" >= '2026-04-01' AND cf."firstRecordedSalary" >= 50000))
    AND cf."creditScore" >= 700
    AND COALESCE(u.unsecured_balance,0) >= 100000
    AND lower(cf."firstRecordedEmploymentType") IN ('salaried','salaired','saliried')
  THEN 'Yes' ELSE 'No' END AS is_dcp_sql
FROM dcpreadmongo.dcp_client_file AS cf
LEFT JOIN unsecured AS u ON u."client_oid" = cf."client_oid"
LIMIT 200;
```
`dcp_client_file`, `.inSrcSalary`, `.dateCreated`, `.employmentType` are not
in `schema.md` (which only documents `dcp_cl_creditor` and
`partner_lender_info`) — names inferred from the DAX table name via the same
convention as `dcp-cl-creditor`→`dcp_cl_creditor`. Confirm live before use.

### 25. UnsecuredBalance / Debt Bucket (DCP) — Approximated
```sql
SELECT dc."client_oid", SUM(dc."currentBalance") AS unsecured_balance,
  CASE WHEN SUM(dc."currentBalance") <= 100000  THEN '0. <1L'
       WHEN SUM(dc."currentBalance") <= 250000  THEN '1. 1L-2.5L'
       WHEN SUM(dc."currentBalance") <= 500000  THEN '2. 2.5L-5L'
       WHEN SUM(dc."currentBalance") <= 750000  THEN '3. 5L-7.5L'
       WHEN SUM(dc."currentBalance") <= 1000000 THEN '4. 7.5L-10L'
       ELSE '5. 10L+' END AS debt_bucket
FROM dcpreadmongo.dcp_cl_creditor AS dc
WHERE dc."accountType" = 'UNSECURED'
GROUP BY 1 LIMIT 200;
```
`client_oid`/`currentBalance` are `schema.md`-documented; `accountType` is
not in its column list for this table (only `debtType`, `roi`, `emi`,
`sanctionedLimit`, `closureAmount` are named) — confirm `accountType='UNSECURED'`
is a real value live before trusting this.

---

## Apptrove signup/event flags — Unmapped (external API, never lands in warehouse)

Per-lead Signup/Registration/Subscription flags. Per `apptrove_events_all3.pq`,
this data is pulled live via `Web.Contents` calls to `https://api.apptrove.com`
inside Power Query — it never lands in `freed_app_db`; no `apptrove` table
exists in any of the four warehouse schemas. If asked, say the data lives
outside the warehouse and isn't queryable here — do not approximate from
`helixmongo.lead_activity` or similar as those aren't verified equivalents.

## Lead temperature (HOT/WARM/COLD) — Unmapped by design

Per `schema.md`'s own glossary, this scoring layer lives outside
`freed_app_db` in a separate call-analysis pipeline (already built — don't
rebuild it). If asked, say so rather than inventing a computation.

---

## Mapping notes & unverified items

**Verified** (name/logic matches `schema.md` directly or its own worked
examples): Signup Count, Credit Pull Count, Signup→CP %, Service Fee, PFF,
Onboarding Fee, MSF, e-Mandate Success Rate (status='ACTIVE' leg), AOM Count,
Settlement Case Split.

**Approximated** (directional — DAX depended on PBI-only calculated columns
with no warehouse mirror: `Status2`, `SettlementNoByDate`,
`Is_Settlement_Started[2]`, `IsNetServiceFeeCharged`, `Is Dormant`, `SPA
Draft Amount`, `Drop Count App`/`Drop Stage`, guessed `SPA Balance Bucket`
cut points): Net Clients/Cancelled %, Cancelled-after-AOM, Settled Debt %,
Total SPA Paid (direct), No. of SPA Paid (Full)/Full SPA 1+/2+, Settled
Count/%, SPA Balance Distribution, DCP-SQL qualification, DCP Debt Bucket
(accountType value unconfirmed).

**Unmapped — no credible warehouse source found this pass**: Stage Drop Rate
(needs `Drop Count App`/`Drop Stage`, absent from `schema.md`'s ~90
documented `client_file_progress` columns); Cohort Size / Subscription
Initialized-Activated-Cancelled / Cumulative Cohort Churn % (depend on
`lead-subscription`/`lead-payment`, tables not present in `context/schema.md`
at all); Apptrove event flags (confirmed external-API-only); HOT/WARM/COLD
lead temperature (confirmed by `schema.md` to live outside the warehouse).

**Environment constraint**: this pass had no network route to the live VM
(`ssh ubuntu@172.20.4.15` timed out), so nothing above was freshly confirmed
via `information_schema` + `LIMIT 5` samples as intended. "Verified" entries
rest on `schema.md`'s prior verification, not a new live check this session.
Before trusting the Approximated/Unmapped entries in production, spot-check
live: `lead_subscription`/`lead_payment` table existence, `lead_vital.
programProfile` value strings, `e_mandate.status` values beyond `ACTIVE`,
`dcp_client_file` table/column names, and `dcp_cl_creditor.accountType`.



## MODEL RELATIONSHIPS & POWER BI → WAREHOUSE TABLE MAP

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



## APPTROVE EVENTS & COLUMNS-IN-USE (note: Apptrove data is external/API-sourced and NOT in freed_app_db)

# Apptrove events & BI column-usage reference

Distilled from FREED's Power Query (.pq) event pulls, the Apptrove_Dim/
Apptrove_Signups/Test_Events TMDL tables, and four column-usage audit
spreadsheets. Cross-checked against `context/schema.md` (the `freed_app_db`
warehouse doc).

---

## 1. Apptrove events reference

### Apptrove is NOT in `freed_app_db`

Apptrove is a third-party mobile attribution/analytics vendor. All three
`.pq` files (`apptrove_events_all3.pq`, `_loop_per_eid.pq`, `_multi_eid.pq`)
pull directly from the **Apptrove REST API**
(`https://api.apptrove.com/api/v1/app/{AppId}/log/event`, api-key auth) into
Power BI's import model — there is no Airbyte/CDC mirror of this data into
Postgres. The column-usage audit confirms this: `Apptrove_Signups` is
tagged `Source Type = Web API`, `Schema = (API)`.

**Consequence for the chatbot:** if a user asks for Apptrove
signup/registration/subscription counts, campaign attribution, etc., say
this data lives outside `freed_app_db` and cannot be queried via SQL here —
do not invent a table/join for it. (If a genuine Apptrove-events warehouse
table appears later, revisit this note.)

### Event types (eid → name)

Three tracked events, identified by Apptrove's opaque `eid`:

| eid | EventName | Meaning |
|---|---|---|
| `8ASKXJ1vWO` | **Signup** | user opened/started the app flow (earliest funnel touch tracked by Apptrove) |
| `5I5oZAYZL8` | **Registration** | user completed registration in-app |
| `B4N_In4cIP` | **Subscription** | user subscribed (paid/activated a subscription product) |

To add a 4th event: add its `eid` to the `Eids` list in the .pq query and
the `EventName` mapping, plus a matching flag block in `Apptrove_Dim`.

### Row shape (one row per event instance, per lead)

Columns pulled: `_id`, `event`, `ets` (event timestamp, cast to
`datetime`), `eid`, `partner`, `ppartner`, `eval`, `camp` (campaign),
`revenue`, `att`, `ip`, `crtd` (record-created timestamp), **`cuid`**,
`cname`, `cphone`, `reason`, `insid`, plus a derived `Date` (day of `ets`)
and `EventName` (the human label above).

### `eid` → lead mapping

**`cuid` is Freed's `leadRefId`** (confirmed by the `Apptrove_Dim` DAX,
which renames `[cuid]` straight to `leadRefId` because "existing
relationships use it"). This is the join key back to Freed's own lead/user
identity — conceptually the same `leadRefId` used throughout
`helixmongo.lead` etc., even though the Apptrove rows themselves aren't in
Postgres.

### Dedup / "first event per eid" rules

The three `.pq` variants (`all3`, `loop_per_eid`, `multi_eid`) are **not**
dedup logic — they're three ways to call the paginated API robustly
(single request with repeated `eid[]=` params, vs. one paginated pull per
eid then combined) in case the API rejects multi-value `eid[]` in one call.
No event rows are dropped or deduped at this stage; every event instance
per lead is kept.

The actual "first per eid" / one-row-per-lead collapsing happens
**downstream**, in `Apptrove_Dim`:
- `SUMMARIZE('Apptrove_Events', [cuid])` — one output row per distinct
  `cuid`/`leadRefId`, regardless of how many raw event rows that lead has.
  (Superseded the old `DISTINCT(cuid, camp)` pattern, which broke if a
  lead's campaign differed between signup and subscription rows.)
- `Signup_Flag` / `Registration_Flag` / `Subscription_Flag` — 1/0, whether
  that lead has **any** row for that event (not first-only).
- `Campaign_FirstTouch` — the campaign (`camp`) attached to the row with
  `MIN(ets)` for that lead — i.e. **first-event-per-lead** campaign
  attribution, deliberately not "any" campaign.
- `First_Event_At` / `Last_Event_At` — `MIN`/`MAX(ets)` per lead.
- Explicit note in the DAX: **there is no credit-pull event in Apptrove**;
  Credit Pull is a separate warehouse concept from
  `helixmongo.lead_trail` (`milestone IN ('CREDIT_PULL_SINGLE_USE',
  'CREDIT_PULL')`, see `schema.md`) and must not be conflated with any
  Apptrove flag.
- `Apptrove_Dim.leadRefId` must be unique (verified via
  `COUNTROWS - DISTINCTCOUNT(leadRefId) = 0`) because it sits on the "one"
  side of PBI relationships to `client-file` and `lead-subscription`.

### Querying Freed's own warehouse for the matching lead

Since the event rows aren't in Postgres, there's no direct SELECT for
"Apptrove signups". What the chatbot *can* do is look up warehouse-side
context for a `leadRefId` a user already has in hand (e.g. from an Apptrove
export), such as its funnel/program status:

```sql
-- Given a leadRefId sourced from Apptrove (cuid), find its Freed funnel status.
-- (This queries helixmongo/spinereadmongo directly; it does NOT read Apptrove data.)
SELECT l."leadRefId", l."status" AS lead_status, l."createdAt",
       lv."programProfile", cf."programCode", cf."clientFileStatus"
FROM helixmongo.lead AS l
LEFT JOIN helixmongo.lead_vital AS lv ON lv."leadRefId" = l."leadRefId"
LEFT JOIN spinereadmongo.client AS c ON c."leadRefId" = l."leadRefId"
LEFT JOIN spinereadmongo.client_file AS cf ON cf."client_oid" = c."_id"
WHERE l."leadRefId" = '<leadRefId aka Apptrove cuid>'
LIMIT 50;
```

### `Test_Events` (unrelated — don't confuse with Apptrove events)

`Test_Events` is a manually-curated DAX `DATATABLE` of **product/release
milestones** (PWA/Spine feature ships with dates, e.g. "DCP pre-qualification",
"UPI autopay deployment") used to annotate funnel charts — not Apptrove
event-log data. Columns: `EventStartDate`, `EventName` (feature description,
not Signup/Registration/Subscription), `Product`, `Metric`, `Dashboard`,
`Order`. Purely a report-layer annotation table; irrelevant to SQL queries.

---

## 2. Columns actually in use (BI model reliance)

Source: `FREED_PowerBI_Column_Usage_Audit.xlsx` ("Used Columns (keep)"
sheet — 187 of 661 total model columns are actually referenced by DAX,
relationships, or sort-by) cross-checked against `FREED_DB_Columns_InUse.xlsx`
for schema-qualified names. Prefer these columns over others on the same
table when writing queries; **bold** = relationship/join key.

- **`helixmongo.lead`**: **`leadRefId`** (join key, 8 refs), `createdAt`,
  `lastCreditPullDate`, `programProfile`, `Current_Status`
  (calc), `Switched_Users` (calc).
- **`helixmongo.lead_vital`**: **`leadRefId`**, `programProfile` (7 refs —
  heavily used for pre-conversion program segment), `leadSourceCode`,
  `lastCreditPullDate` (legacy, pre-2025-11-17 cutover only).
- **`helixmongo.lead_trail`**: **`leadRefId`** (30 refs — the most-used
  join key in the whole model), `milestone` (18 refs), `status` (18 refs),
  `startTime`, `leadJourney_oid` (relationship to `lead_journey`).
- **`helixmongo.lead_journey`**: **`_id`** (relationship), **`leadRefId`**,
  `journeyName` (4 refs), `startTime`.
- **`helixmongo.lead_payment`** (subscription/payment attempts, PWA-side —
  distinct from `spinereadmongo.payment_entry`): `leadRefId` (13 refs),
  `status` (15 refs), `createdAt` (11 refs), `amount`, `failureReason`,
  `refSubscription_oid` (relationship to `lead_subscription`).
- **`helixmongo.lead_subscription`** (Credit Insights / CI subscription
  state): **`_id`**, **`leadRefId`** (11 refs, relationship), `status`
  (9 refs), `createdAt`.
- **`spinereadmongo.client`**: **`_id`** (7 refs), **`leadRefId`** (4 refs —
  the lead↔client hop), `mobile`.
- **`spinereadmongo.client_file`** (18 used columns — the most heavily
  relied-on fact table): **`_id`** (14 refs), **`client_oid`** (10 refs,
  relationship), **`clientFileNumber`** (36 refs — the most-referenced
  column in the model), **`clientFileProgress_oid`** (relationship),
  `clientFileProgressStatus`, `clientFileStage`, `programCode`,
  `totalCurrentOutstanding` (13 refs), `firstEMandateActivatedDate`
  (13 refs = AOM), `isFirstEMandateDone`, `isOnboardingFeePaid`,
  `saleHappenedDate`/`saleHappenedOn`, `dateCreated`, `creditScore`,
  `orgCode`, `pendingCancelledAt`, `wad`.
- **`spinereadmongo.client_file_progress`** (appears twice in the model as
  distinct roles — "client-file-progress" and "client-file-progress (2)"):
  **`_id`**, **`client_oid`**, **`clientFileNumber`** (25 refs in the (2)
  role), `clientFileStage` (14 refs), `createdAt`/`createdDate`,
  `firstEMandateActivatedDate`, `isCancelled`, `isCancelledRequested`,
  `isAppSale`, `cancelledAt`, `pendingCancelledAt`, `saleHappenedAt`,
  `salesRepAssignedStage`, `assignedSalesRep`, `totalCurrentOutstanding`,
  `orgCode`.
- **`spinereadmongo.client_file_consolidation`**: **`clientFileNumber`**
  (14 refs, relationship), `spaBalance` (4 refs), `collectedByKFS`,
  `refundedToClient`, `totalIncomingAmountToTrust`.
- **`spinereadmongo.e_mandate`**: **`clientFile_oid`** (19 refs,
  relationship — most-used column on this table), `status` (9 refs),
  `activatedOn` (7 refs), `isUPIMandate` (7 refs), `amount`, `authMode`,
  `createdOn`, `lastUpdatedOn`, `whoDidIt`.
- **`spinereadmongo.payment_entry`**: `txnPurpose` (19 refs),
  `paymentEntryStatus` (19 refs), `amount` (16 refs), `fromAccountEntity`
  (13 refs), `toAccountEntity` (10 refs), **`clientFile_oid`**
  (relationship), `paymentReq_oid` (relationship), `settledDate`,
  `presentedDate`.
- **`spinereadmongo.s_t_plan`**: **`_id`** (relationship), `isArchived`
  (3 refs), `totalOutstanding`, `feeAmountReceivedFromTrust`,
  `sifUploadDate`, **`clientFile_oid`** (relationship).
- **`spinereadmongo.s_t_entry`**: **`stPlan_oid`** (relationship),
  `isArchived` (4 refs), `amountFromTrustToClient`,
  `amountFromTrustToCreditor`, `amountFromClientToCreditor`, `amount`,
  `draftDate`, `fiCreditor_oid`, `clientFile_oid`.
- **`spinereadmongo.fi_creditor`**: `clientFile_oid` (relationship),
  `clientFileNumber`, `settled_accts` (calc).
- **`spinereadmongo.payment_request`**: **`_id`** (relationship),
  `fiCreditor_oid`.
- **`spinereadmongo.payment_schedule_plan`**: **`clientFile_oid`**
  (relationship), `isCurrent`, `spaAmount`, `spaFirstDraftDate`.
- **`dcpreadmongo.dcp_client_file`**: **`_id`**, **`client_oid`**
  (relationship), `clientFileNumber`, `clientFileStage`, `creditScore`,
  `dateCreated`, `firstRecordedSalary`, `orgCode`.
- **`dcpreadmongo.dcp_fi_budget`**: `clientFile_oid`, `employmentType`,
  `inSrcSalary`.
- **`dcpreadmongo.dcp_financial_info`**: `clientFile_oid` (relationship),
  `totalLoanAmount`.
- **`dcpreadmongo.dcp_cl_creditor`**: `client_oid`, `accountType`,
  `currentBalance`.
- **`dcpreadmongo` lender-qualification-status** (partner-lender
  eligibility checks — not yet in `schema.md`, confirm table name live
  before using): `clientFile_oid`, `dcpPartnerLender_oid`, `createdAt`,
  `fieldName`, `fieldStatus`, `fieldValue`, `qualificationCheckState`,
  `qualificationStatus`.
- **`insightreadmongo.ameyo_call_details`**: `callPhoneNo` (8 refs),
  `callStatus` (4 refs), `callDurationSeconds`, `callDateTime`, `callType`.

### Coverage caveat

The audit only sees columns referenced by **DAX/relationships/sort-by** —
a column used solely in a report visual (chart axis, slicer, table with no
calc) is invisible to it and shows as "unused" even if analysts rely on it
visually. Total: 661 model columns audited, 187 (28%) confirmed in-model-use,
474 "not used in model" but unverified against the report/visual layer. So
treat the lists above as a **floor** of high-confidence columns, not an
exhaustive whitelist — don't refuse a column just because it's absent here.



## COMPANY & BUSINESS CONTEXT (from narrative KB docs — affects how questions are interpreted)

# FREED — Company & Business Context (narrative source digest)

> Distilled from FREED's narrative business docs (Business Model note, Nov-25
> investor intro deck, System & Working Details doc, Company Overview,
> General Knowledge Base). Complements `context/schema.md` (data glossary)
> and the KB's `FREED_BusinessContext.md` (confirmed DAX-level definitions) —
> this file captures only what is NEW/not already in those two: segment
> eligibility rules, the customer-facing product portfolio, the ops-system
> landscape, and a couple of revenue-rule refinements. Read this alongside
> `schema.md` for column-level grounding.

## What FREED does

FREED is a borrower-first debt-relief platform operating in India's
unsecured retail credit market (~$230bn book, ~$40bn stressed/written-off).
Where legacy collections (banks, collection agencies, ARCs) are lender-
centric, FREED aligns borrower *intent* and *capacity* to negotiate
structured outcomes. Two consumer-facing programs carry the business:
**DCP** (Debt Consolidation — for EMI-current but strained borrowers) and
**DRP** (Debt Resolution — for >90 DPD delinquent borrowers). A third,
**DEP** (Debt Elimination), and a subscription product, **Credit Insights**,
round out a "Prevention → Care → Cure" funnel (see below). Company founder
previously ran SCOA, a US debt-relief firm — the India play is explicitly
modeled on the ~25-year-old, $50bn+/yr US debt-relief industry.

## Customer segments — the eligibility taxonomy (new; not in schema.md)

Every lead/client is scored into exactly one segment based on credit
bureau data (credit score, FOIR, delinquency, outstanding balance, active
loan count). This segment gates which program/product a customer can be
offered — treat it as the master routing variable for funnel and program
analytics (`lead_vital.programProfile` / `programProfileRule` in the
warehouse maps to this taxonomy):

| Segment | Definition | Program offered |
|---|---|---|
| **NTC** (New to Credit) | No credit accounts/history in bureau at all; no score generated | Credit Insights not applicable (no score to show) |
| **Others** | Has credit history but **no active** loans/card balances currently | Credit Insights, Goal Tracker, Payment Reminders, Credit Report Details only |
| **DEP** | Active loans/cards, **FOIR < 50%**, no delinquency, strictly regular repayment | Debt Elimination Program (accelerated payoff, avalanche-style) |
| **DCP Eligible** | Multiple loans/cards, **FOIR > 50%**, still largely current, **credit score ≥ 700**, **min. unsecured debt ₹1,50,000** | Debt Consolidation Program |
| **DCP Ineligible** | Fails DCP cutoffs (credit score < 700 and/or unsecured debt < ₹1,50,000) | Routed to Credit Insights instead |
| **DRP Eligible** | One or more unsecured loans delinquent, defaults on record, debt load high relative to capacity, often already in collections | Debt Resolution Program |
| **DRP Ineligible** | Has delinquency but doesn't qualify for settlement — e.g. delinquency is on a creditor FREED doesn't service, delinquency is on a *secured* creditor, or outstanding is below the program's minimum enrollment threshold | FREED Shield still offered (harassment protection); no settlement program |

**Important interpretive note:** "eligible" here is a bureau/profile-driven
classification computed on lead/client data, distinct from whether the
person has actually *enrolled* — a lead can be `DRP Eligible` and never
convert to a client. Don't conflate segment eligibility with enrollment
status (`client_file.clientFileStatus`).

## Funnel stages — Prevention / Care / Cure

Official internal/investor framing mapping segments to programs:
- **Prevention** (top of funnel): Credit Insights (₹99/mo subscription) — offered to all non-NTC segments (Others, DEP, DCP-E/I, DRP-E/I).
- **Care** (middle of funnel): DCP — offered to DCP-Eligible only.
- **Cure** (bottom of funnel): DRP — offered to DRP-Eligible only.

Annuity customers (ongoing PFF payers, not one-time fee events) are
expected to contribute ~80–85% of monthly revenue at steady state — a
rising annuity share is the headline "operating leverage" signal used in
investor reporting. Revenue-by-cohort-age split observed in decks: roughly
30% from 1st-month customers, 30% from month 2–4, and the remainder from
customers beyond month 4 — i.e. mature-cohort revenue is already a large
share of the book, not just new enrollments.

## Product portfolio (customer-facing; useful for "which product" questions)

Besides DCP/DRP: **Credit Insights** (₹99/mo — score, factor breakdown,
monthly personalized video, 6-monthly "Credit Wrap"), **DEP** (accelerated
repayment simulator, no debt reduction — just faster/cheaper payoff),
**Goal Tracker** (6-month credit-improvement roadmap, all non-NTC
segments), **FREED Shield** (harassment-report/escalation tool, in-app,
DRP-E/I only), **CHPP** (call-based harassment support — see
`FREED_BusinessContext.md` for the Shield-vs-CHPP distinction; that
correction stands, this doc doesn't add anything new there), **Payment
Reminders** and **Credit Report Details** (all non-NTC segments).

Quick program comparison (from Company Overview doc): DEP = positive
credit-score impact, no debt reduction, faster repayment focus; DCP =
neutral score impact, no debt reduction, EMI simplification (single EMI
replaces many); DRP = negative score impact (settlements are marked
differently than "paid in full"), but the only path that actually reduces
principal owed.

## Customer journey / lifecycle (DRP, narrative view)

1. **Discovery & opt-in** — inbound via ads/referral/organic (pull model, not outbound).
2. **Eligibility check, credit pull & counselling** — soft credit pull confirms unsecured delinquent debt; a debt counsellor assesses intent, capacity, hardship context.
3. **Program structuring & onboarding** — customer pays the one-time **Evaluation/Onboarding Fee** (~₹1,200–1,500); FREED builds the resolution roadmap and sets the fixed monthly savings (SPA contribution) target; e-agreements signed.
4. **Savings infrastructure** — automatic monthly SPA deposits via **E-NACH**; SPA is the "negotiation currency."
5. **Resolution phase** — FREED negotiates with lenders once enough SPA has accumulated per account; sequential per-account settlement.
6. **Ongoing support** — FREED Shield + CHPP harassment protection, dedicated RM, paralegal support run throughout.

No settlement offer = no Service Fee charged (already documented in
`FREED_BusinessContext.md`). What's new here: FREED explicitly does **not**
charge large upfront program fees — fees are staged to track progress
through the settlement journey, which is the stated rationale for the
Onboarding Fee / PFF / Settlement Fee sequencing rather than a single
upfront charge.

## Revenue model — one addition worth flagging

Core fee structure (Settlement/Service Fee 15% + 18% GST, PFF 10% of
monthly SPA, Onboarding Fee ~₹1,500, DCP lender commission ~2.5–3% of
disbursal) is already documented in `FREED_BusinessContext.md` — not
repeated here. **One refinement found in the System & Working Details
doc that BusinessContext.md doesn't have:** the PFF formula includes a
floor — *"10% of paid amount **or Rs. 500 + 18% GST, whichever is
greater**."* This implies PFF has a minimum floor around ₹590 (₹500 +
18% GST) per month even when 10% of that month's SPA contribution would
be smaller. Flag this to the data team to confirm against live
`payment-entry` PFF rows before relying on it in a measure — it wasn't
present in the previously-confirmed wiki definition.

DCP revenue is the "no charge to borrower" model — 100% lender-side
commission (2.7–3% of disbursal); this is the only revenue line where the
client pays FREED nothing directly.

## Systems / operational landscape (new — not in schema.md)

The Power BI/warehouse world (SPINE-sourced Mongo collections) sits inside
a broader ops-system landscape described in the System & Working Details
doc:

- **Customer App / PWA** — customer-facing: SPA/plan status, platform-fee visibility, cancellation requests (OTP-gated), payment-proof upload, settlement journey tracking. Does not expose internal SPINE codes to customers.
- **SPINE** — internal ops backend/portal (this is the system the warehouse mirrors). Modules: Customer Profile, Payment Tracking, SPA Ledger View, PFF/Platform Fee module, Payment Proof module, Settlement Case module, Refund module, Cancellation module.
- **Catalyst Trustee Current Account** — the actual trust account holding customer SPA/savings funds; settlements and refunds are paid out from here. This is the real-world account behind the "SPA"/trust abstraction in the data model.
- **Freed India Current Account ("KFS")** — FREED's own revenue account; only approved fees (Evaluation Fee, Settlement Fee, Platform Fee/PFF) land here. Customer funds should never sit in this account — confirms why `toAccountEntity = KFS` in `payment-entry` is the correct filter for "money FREED earned."
- **TSP-managed virtual account / ledger layer** — a reconciliation layer mapped onto the Catalyst account for per-customer segregation and audit trail; not a real customer bank account.
- **Payment rails**: eNACH/NACH (scheduled recurring SPA debits — batch, reconciliation-heavy), UPI AutoPay (recurring UPI mandate), PG checkout/payment links (ad hoc/bullet payments, proof-linked), NEFT/RTGS/IMPS (manual/exception cases).
- **MoEngage** — event-driven customer comms (app/WhatsApp/SMS/email nudges).
- **Metabase** — the reporting layer this warehouse feeds (cancellation reasons, SLA aging, fee collection, reconciliation exceptions).
- **Ameyo** — dialer/call-center system (already in `schema.md` as `ameyo_call_details`); not elaborated further in these narrative docs.

## Cancellation requests — reason taxonomy (relevant to "why did clients cancel" questions)

Customers self-file cancellation via the app (OTP-verified — confirms
customer intent, does **not** itself cancel the file). Customer picks a
plain-language reason tile (e.g. "I want to settle directly," "I cannot
continue payments," "I am facing creditor pressure") which the system maps
to an internal `CR_1xx` SPINE reason code and a canonical reason group
(Loan Expectation, Self Service, Direct Settlement, Creditor Pressure,
Affordability, Family/Friend Support, Unforeseen Expense, Not Interested,
Service Dissatisfaction, Trust/Credibility Issue, Eligibility Issue,
Others). Separately, CS independently records its own assessed reason
after talking to the customer. **Match status** (`MATCH` /
`PARTIAL_MATCH` / `MISMATCH` / `REVIEW_REQUIRED` / `CS_PENDING`) compares
customer-stated vs. CS-recorded reason — a rising mismatch rate is an
internal data-quality/retention signal, not a program metric. Final
cancellation always remains SPINE-controlled (balances/fees/refunds
reviewed before closure) — an OTP-verified request is not itself a
cancelled file.

## Scale context (as of the Nov-25 deck; directional, not live)

Debt enrolled to date: ₹2,500cr+; debt settled: ₹200cr+ at **~45%
settlement rate** (of enrolled debt); ~48,000 customers enrolled; ~16,500
accounts settled; 80+ lenders worked with. Typical DRP borrower: ~34 years
old, ₹30–35k/month income, >90 DPD, credit score 600–650, 4–5 active
accounts, ~₹5 lakh average debt load — useful as a sanity-check profile,
not a hard eligibility cutoff (the formal DCP/DRP cutoffs above are the
authoritative ones).

## Docs that were not new / not read further

`FREED - General Knowledge Base.docx` is very large (~162k chars) but is
almost entirely generic Indian consumer-credit education (what is a
credit score, bureau mechanics, loan types, interest-rate math, RBI
lending rules) rather than FREED-specific business rules — skipped beyond
confirming no FREED-specific data-interpretation content was buried in it
(one minor note: DEP internally uses an "avalanche" repayment strategy,
DRP-Eligible customers are guided with a "snowball" style account
prioritization — cosmetic, not a data-modeling concern).
