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
