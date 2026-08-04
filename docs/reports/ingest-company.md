# Ingest report — context/_ingest/company.md

## Docs read

All 5 source docs were readable (pypdf for PDFs, python-docx for DOCX;
tables in `.docx` files were also extracted):

| Doc | Readable | Notes |
|---|---|---|
| FREED Business Model.pdf | Yes | 4 pages, 5,352 chars extracted |
| FREED Intro Nov-25 1.pdf | Yes | 18 pages, 14,446 chars extracted (26MB file is mostly embedded images; text layer was still present and sufficient) |
| FREED_System_and_Working_Details.pdf | Yes | 8 pages, 19,427 chars extracted |
| FREED — Company Overview.docx | Yes | 51,777 chars extracted (incl. tables) |
| FREED - General Knowledge Base.docx | Yes | 161,678 chars extracted; ~95% is generic Indian consumer-credit education (bureau mechanics, loan types, interest math, RBI rules), not FREED-specific — skimmed in full but excluded per "skip marketing fluff" instruction |

Also reviewed `FREED_BusinessContext.md` (KB folder) and `context/schema.md`
(project) first to identify what was already captured, to avoid duplication.

## Key business rules extracted (new, not already in schema.md / BusinessContext.md)

1. **Customer segmentation taxonomy** — NTC / Others / DEP / DCP-Eligible /
   DCP-Ineligible / DRP-Eligible / DRP-Ineligible, with precise eligibility
   cutoffs (FOIR <50% for DEP, FOIR >50% + credit score ≥700 + min.
   unsecured debt ₹1,50,000 for DCP-Eligible, delinquency-based rules for
   DRP-Eligible/Ineligible). This is the master routing variable behind
   `lead_vital.programProfile` and was not previously documented at this
   level of detail.
2. **Funnel stages** (Prevention/Care/Cure) with product mapping and
   annuity-revenue mix stats (annuity customers ~80-85% of steady-state
   revenue; cohort-age revenue split ~30/30/40 across month-1 / 2-4 /
   4+).
3. **Product portfolio** beyond DCP/DRP: Credit Insights (₹99/mo), DEP,
   Goal Tracker, Payment Reminders, Credit Report Details, FREED Shield —
   with segment-eligibility gating for each.
4. **PFF formula refinement** — System & Working Details doc states PFF =
   "10% of paid amount **or Rs. 500 + 18% GST, whichever is greater**" —
   a floor rule not present in the previously-confirmed wiki PFF
   definition. Flagged in company.md as needing data-team confirmation
   against live `payment-entry` rows before use in a measure.
5. **Ops-system landscape** — SPINE (internal backend), Catalyst Trustee
   Current Account (real account behind "SPA"), Freed India Current
   Account = KFS (fee-only, no customer funds), TSP virtual ledger layer,
   payment rails (eNACH/UPI AutoPay/PG/NEFT-RTGS-IMPS), MoEngage, Metabase.
6. **Cancellation reason taxonomy** — customer-facing reason tiles →
   `CR_1xx` SPINE codes → canonical reason groups, OTP-gated but does not
   itself cancel the file, and a customer-vs-CS reason "match status"
   field (`MATCH`/`PARTIAL_MATCH`/`MISMATCH`/`REVIEW_REQUIRED`/
   `CS_PENDING`) worth knowing when interpreting cancellation analytics.
7. Scale/context numbers from the Nov-25 deck (₹2,500cr+ debt enrolled,
   ₹200cr+ settled at ~45% settlement rate, ~48,000 customers, ~16,500
   accounts settled, 80+ lenders) and the typical DRP borrower profile,
   included as sanity-check reference only.

## Output

`context/_ingest/company.md` — 12,185 characters (slightly above the
6-10k target given the density of new segment/eligibility rules worth
preserving verbatim-ish; trimmed everything else, especially skipping the
General Knowledge Base's generic credit-education content and not
repeating anything already in `schema.md` / `FREED_BusinessContext.md`).
