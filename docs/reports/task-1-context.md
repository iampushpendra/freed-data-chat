# Task 1 — context/schema.md

**Status:** DONE

## What was verified live (VM SSH → psycopg2 against freed_app_db)
- Full schema list (22 non-system schemas) and per-table row estimates
  (`pg_class.reltuples`) across all schemas.
- Columns + data types for 25 tables: helixmongo.{lead, lead_vital,
  lead_source, lead_activity, lead_journey, lead_program_details,
  lead_address, contact, utm_params}; spinereadmongo.{client, client_file,
  client_file_progress, client_file_consolidation, s_t_plan, s_t_entry,
  s_t_fee_entry, payment_entry, fi_creditor, cl_creditor, e_mandate,
  fi_document, fi_summary}; dcpreadmongo.{dcp_cl_creditor,
  partner_lender_info}; insightreadmongo.ameyo_call_details.
- Confirmed: all business/date columns are `character varying` (Airbyte
  mirror of Mongo), not native timestamp/boolean types; sampled distinct
  values for `programCode`, `clientFileStatus`, `lead.status`,
  `isArchived`, `isSettled`, `txnPurpose` to ground the glossary in real
  data (e.g. `txnPurpose` values SPA/ONBOARDING_FEE/MSF/PFF/SERVICE_FEE/
  SETTLEMENT confirmed with row counts).
- Corrected one KB claim in passing: `s_t_plan.isArchived` is text
  (`'true'`/NULL), not boolean — documented as `IS DISTINCT FROM 'true'`.

## Taken from KB (not independently re-verified beyond cross-checking
column names against live schema)
- Business definitions: SPA, Platform/Direct Settlement (3 cases), Service
  Fee/GST math, PFF, MSF, Onboarding Fee, KFS, AOM, Funnel stages
  (Prevention/Care/Cure), fi-creditor vs cl-creditor scope — all from
  `FREED_BusinessContext.md`.
- The join-key pitfall (`client_file.client_oid → client._id`, not
  `lead.leadRefId` directly) and the lead→client_file fan-out warning —
  from `lead_signup_mandate_extract_REVIEW.sql` / `lead_extract_CHUNKING.sql`.
  Table/column names in these examples were re-verified against the live
  schema and match.
- Did not open the .xlsx files (FREED_DB_Tables_Columns.xlsx,
  FREED_DB_Columns_InUse.xlsx) — live introspection plus the KB markdown/
  SQL files already gave complete, verified column-level coverage for the
  ~25 priority tables, so xlsx cross-check was judged unnecessary rather
  than skipped due to a read failure. Gap: did not confirm which columns
  the Power BI model specifically uses (in-use vs unused) — schema.md
  documents all columns observed live, slightly broader than the BI
  model's actual usage.

## Output
`/Users/pushpendrasingh/projects/freed-data-chat/context/schema.md` —
19,749 characters (within the 12k–20k target). No other files were
created or modified; no git commit made.

## Concerns
- HOT/WARM/COLD lead temperature (mentioned in prior project memory) is
  not in `freed_app_db` — schema.md explicitly tells the LLM to say so
  rather than fabricate a computation, instead of documenting it as if
  present.
- `dcp_cl_creditor` vs `cl_creditor` overlap noted in the KB as unresolved
  ("confirm which table a given measure should point at") — carried into
  schema.md as-is rather than resolved, since live data alone can't settle
  which one a given BI measure intends.
- Row-estimate counts are `reltuples` (post-last-ANALYZE estimates), not
  exact — flagged in the doc as approximate.
