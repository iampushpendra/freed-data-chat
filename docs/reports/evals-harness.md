# Query-evaluation harness — build report

## What was built

- `evals/golden.yaml` — versioned golden set (`version: 1`), 22 cases.
- `evals/run_evals.py` — pure-stdlib Python 3 harness with `--canonical-only`
  (default) and `--full` modes, `--json` report output, scalar/rowcount/
  resultset comparison helpers, and a small hand-rolled YAML loader (no
  `pyyaml` dependency).
- README.md — new "Evaluating answer quality" section.

No files under `api/`, `vm/`, or `web/` were touched. No commit was made.

## Case counts

| | count |
|---|---|
| Total cases | 22 |
| Enabled | 15 |
| Disabled (Approximated/Unmapped, skipped by design) | 7 |

## Canonical-only self-test result

Ran against the live query API
(`https://locking-engage-ordered-sic.trycloudflare.com/query`):

```
python3 evals/run_evals.py --canonical-only
```

**All 15 enabled cases ran clean (0 errors), 7 skipped as disabled.** Exit
code 0.

| id | rows back | value |
|---|---|---|
| signups-last-7-days | 1 | 64453 |
| signup-count-july-2026 | 1 | 243734 |
| credit-pull-count-july-2026 | 1 | 221318 |
| signup-to-credit-pull-pct-july-2026 | 1 | 90.8 |
| credit-pull-by-month-2026 | 8 | monthly series, Jan–Aug 2026 |
| active-clients-by-program | 2 | DRP 4931 / DCP 52 |
| leads-convert-dedup | 1 | [535256 rows, 476455 distinct leads] |
| settlement-case-split | 4 | Unclassified 79.5% / Case2 14.6% / Case1 4.2% / Case3 1.8% |
| emandate-success-rate | 1 | [93990 success, 120488 total, 78.01%] |
| aom-count-july-2026 | 1 | 7900 |
| pff-total-last-quarter | 1 | 25,782,959 |
| service-fee-net-july-2026 | 1 | 230.62 lacs |
| pff-net-july-2026 | 1 | 85.54 lacs |
| onboarding-fee-net-july-2026 | 1 | 24.79 lacs |
| msf-legacy-total | 1 | 66,332,983.96 |

Query latency ranged 0.4s–43.8s (the two `lead_trail`-scanning aggregates —
signup/credit-pull percentage and the by-month series — were the slowest, as
expected for a ~20.5M-row event table); the harness's default 70s per-request
timeout comfortably covers this.

## KB SQL corrections made

None of the 15 enabled cases' SQL needed correction — every recipe taken
directly from `context/schema.md`'s "Verified" metrics and its "Example
Q → SQL" section ran cleanly against the live warehouse on the first try.
One defensive tweak was made relative to the KB's literal text: metric #8
("Service Fee, net of refund") and the analogous Onboarding Fee case wrap
the `fromAccountEntity='KFS'` subtraction term in `COALESCE(..., 0)`, since
a `NULL` there (no matching refund rows) would otherwise null out the whole
subtraction in Postgres. This doesn't change the metric's logic, only
guards against a NULL-propagation edge case the KB's inline snippet didn't
show defensively.

## Disabled cases (recorded, not graded)

`drp-eligible-credit-pull-pct`, `net-clients-cancelled-pct`,
`settled-debt-pct`, `spa-balance-distribution`, `dcp-sql-qualification`,
`cohort-churn-unmapped`, `hot-warm-cold-lead-temperature` — each carries a
`skip_reason` citing the specific unconfirmed column/table/value the KB
itself flagged as Approximated or Unmapped (e.g. `dcp_client_file`'s
`firstRecordedSalary`/`firstRecordedEmploymentType` aren't in
`context/schema.md`'s documented column list; `lead_subscription`/
`lead_payment` aren't documented in the warehouse section at all; HOT/WARM/
COLD lead temperature is confirmed to live outside `freed_app_db`). These
were intentionally never enabled — their canonical SQL wasn't run as part of
the self-test since there's no verified ground truth to compare a chatbot
answer against.

## Not yet run

`--full` mode (live chatbot end-to-end comparison) was not exercised in this
pass — it requires `CHAT_PASSWORD` for the deployed chatbot, which wasn't
provided as part of this task. The mode is implemented and its `--help`/
argument wiring was verified; the canonical-only self-test above is the
gate this task asked for before enabling any case.
