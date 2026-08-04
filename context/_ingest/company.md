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
