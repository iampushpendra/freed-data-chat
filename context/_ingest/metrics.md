# FREED Derived Business Metrics — Power BI → PostgreSQL Translation

> Source: Power BI DAX (`FREED_NewDAX.md`, `funnel_day_on_day.dax`,
> `Apptrove_Dim_flags.dax`, `tables/*.tmdl`). Ground truth for table/column
> names: `context/schema.md` (live `freed_app_db`; schemas `helixmongo`,
> `spinereadmongo`, `dcpreadmongo`, `insightreadmongo`). **This pass had no
> network path to the live VM** (`ssh ubuntu@<vm-host>` timed out from this
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
(`ssh ubuntu@<vm-host>` timed out), so nothing above was freshly confirmed
via `information_schema` + `LIMIT 5` samples as intended. "Verified" entries
rest on `schema.md`'s prior verification, not a new live check this session.
Before trusting the Approximated/Unmapped entries in production, spot-check
live: `lead_subscription`/`lead_payment` table existence, `lead_vital.
programProfile` value strings, `e_mandate.status` values beyond `ACTIVE`,
`dcp_client_file` table/column names, and `dcp_cl_creditor.accountType`.
