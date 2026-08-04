# Ingest report: Power BI model → context/_ingest/model.md

**Status:** done

**Summary:** Distilled the FREED Power BI tmdl export (relationships.tmdl, model.tmdl, database.tmdl, expressions.tmdl, 41 real table files) into `context/_ingest/model.md`, translating Power BI table/column names to warehouse `schema.table` names from `context/schema.md` wherever a source mapping was determinable.

**Relationships documented:** 29 real entity relationships (all `X.Date → LocalDateTable_*` date-hierarchy noise excluded, ~140 of those skipped per instructions), plus 4 flagged inactive/legacy relationships and 7 Power-BI-only "hub" relationships (Lead Dimension, Apptrove_Dim).

**Tables mapped to warehouse:** 24 of 41 non-date tables mapped to a real `schema.table` (straight mirror or derived/aggregated query); 2 tables (`ClientFact_Lender`, `LenderFact`) flagged as unverified/likely-derived; remaining ~15 are Power-BI-only (calculated tables, measure holders, date/demo tables, or third-party Apptrove attribution data with no warehouse presence) — all listed in the "Unmapped / Power-BI-only" section.
