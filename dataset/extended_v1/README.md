# Tiby finance dataset v1

A runnable synthetic finance dataset and golden question set for the TBX finance-assistant challenge. Built from the repository's original 20,000 transactions, with explicit synthetic business records added for spend, payouts and reconciliation.

**Start with `finance.sqlite` and `golden_cases.csv`.** No API, model, Python packages or network access is required to query the database.

## What is included

| Data | Rows |
|---|---:|
| Original bank transactions, unchanged | 20,000 |
| Additional scenario transactions | 36 |
| Accounts / banks | 1,000 / 10 |
| Vendors | 198 |
| Chart-of-accounts categories | 8 |
| Invoices | 536 |
| Payouts / payout attempts | 536 / 537 |
| Expected ledger entries | 539 |
| Reconciliation cases / confirmed match allocations | 537 / 534 |
| Curated business scenarios | 32 |
| Golden questions | 80 |

The original three CSVs are preserved byte-for-byte in `original/`. Their signed amounts, dates, references, descriptions, account/entity IDs and balances remain unchanged. `bank_transaction` normalizes money to integer paise, maps empty references to SQL NULL and adds provenance. `csv/` contains every extended database table for import into another system. `schema.sql` describes the SQLite schema and its views.

The source commit is `54a98efcf15f62180a2628c59123c0f4306b7882`, on `feat/dataset` in https://github.com/pragya9460/tbx-nightowls. Source hashes and validation results are in `manifest.json`.

## Business conventions — read before using totals

1. This extension is synthetic test data, not additional organizer-supplied facts. The existing repository also describes its original data as synthetic. All 500 legacy transaction enrichments and all new business records are explicitly synthetic.
2. The demo has one organization, `ORG-TIBY`, and one currency, INR. Mapping all 534 existing entity IDs to this company is an explicit fixture assumption; no original entity IDs were rewritten. INR is an added convention, not metadata recovered from the original CSVs.
3. Money is integer paise: `1000000` means INR 10,000.00. Debits are negative; credits are positive. Invoice, payout, expected-entry and match amounts are positive magnitudes. Invoice allocations can be negative to reopen an invoice after a reversal.
4. Evaluation as-of date is **2026-09-05**. “Last month” is **2026-08-01 inclusive through 2026-09-01 exclusive**. Dates have day precision and no implied timezone. September is a partial month.
5. **Gross vendor cash spend** is the magnitude of observed `vendor_payment` allocations. **Net vendor cash spend** subtracts `vendor_refund` allocations. Fees, internal transfers and unclassified legacy cash are excluded. This is cash-basis reporting, not accrual expense or profit. Unknown legacy classifications remain visible as an explicit coverage gap.
6. **Successful payout amount** comes from `payout.status='succeeded'` and the chosen payout date field; it is an operational instruction metric. **Observed vendor cash spend** comes from bank allocations. They can differ because of fees, missing settlement evidence, reversals, suspected duplicates and timing differences. Never silently substitute one for the other.
7. Payout `requested_date` is the instruction date; `settled_date` is the synthetic operational settlement date. Bank cash uses `bank_transaction.transaction_date`; ledger expectations use `expected_entry.expected_date`. A succeeded payout can deliberately lack bank evidence to exercise reconciliation.
8. The unmatched original transactions are **not_assessed**, not automatically **unreconciled**. Missing UTR or reference does not determine payout or reconciliation status.
9. `available_balance_minor` is the original undated account snapshot. Added transactions do not update it. Do not calculate historical balances or claim it reconciles with the transaction history.
10. Vendor identities and categories attached to selected original narrations are synthetic assignments for testing, not verified real-world merchant classifications. Prefixes in the original descriptions are not reliable transaction semantics.

## Files

- `finance.sqlite`: ready-to-query SQLite database with keys, checks, indexes and views.
- `csv/`: 23 relational tables as CSV; SQL NULL is an empty CSV cell.
- `original/`: unchanged source CSVs.
- `schema.sql`: executable SQLite DDL. It creates empty tables/views; use the existing database to avoid an import step. It is not MySQL or PostgreSQL DDL.
- `DATA_DICTIONARY.md`: table meanings, status definitions and safe join patterns.
- `data_dictionary.json`: exact columns, types, primary keys and foreign keys.
- `golden_cases.csv`: compact review sheet with question, SQL and expected result.
- `golden_cases.json`: full contexts, numeric results, evidence queries/results and behavioral expectations.
- [`human_answers/golden_human_answers.md`](human_answers/golden_human_answers.md): all 80 questions with human-facing expected answers, formatted result tables and links to complete evidence CSVs. CSV and JSON versions are in the same directory. These are evaluation labels, not runtime retrieval content.
- `fixtures.json` and `csv/scenario.csv`: scenario IDs and linked records.
- `COVERAGE.md`: mapping to the problem statement and explicit limits.
- `generate.py`: deterministic generator using only Python's standard library.
- `validate.py`: independent consistency assertions and golden-query replay.
- `validation_report.json`: results from validation of this package.
- `demo_questions.md`: short demonstration with exact answers.

## Run

From this directory:

```sh
python validate.py
```

Query using Python:

```python
import sqlite3
db = sqlite3.connect('finance.sqlite')
rows = db.execute("""
SELECT vendor_name, SUM(outflow_minor) AS spend_paise
FROM v_cashflow
WHERE kind = 'vendor_payment'
  AND transaction_date >= '2026-08-01'
  AND transaction_date < '2026-09-01'
GROUP BY vendor_id, vendor_name
ORDER BY spend_paise DESC, vendor_id
LIMIT 5
""").fetchall()
print(rows)
```

Rebuild the generated data in a separate directory:

```sh
python generate.py --source original --out rebuilt
```

`generate.py` builds database, CSVs, dictionaries, cases, hashes and its invariant results. This README, coverage notes, validator and demo are package documentation, not generated by that command. The generator replaces the output directory's `finance.sqlite`, so choose an output directory intended for regeneration.

## Expected workflow for the team

- Dataset owner: review glossary and synthetic assumptions; freeze this version before testing.
- Backend owner: import the database, use typed/parameterized queries, and aggregate before passing results to the model.
- Assistant owner: resolve intent and date/vendor/account scope; use clarification where required; explain results with evidence.
- Evaluation owner: compare numeric results and record sets, test follow-up context and missing-data behavior, and record latency/model usage separately.

Treat these 80 cases as reviewable development labels. They were computed and consistency-checked, not human-certified and not held-out proof of model accuracy. Do not put the golden answers into the assistant's retrieval index or prompt. Split new evaluation families before tuning and keep paraphrases/multi-turn variants in the same split.

## Model and scale limits

This package covers the problem statement's data-dependent functional scenarios within the spend/payout/reconciliation scope, plus explicit missing-data responses. It is not a chat UI or an assistant implementation. It does not establish model efficiency, response latency, production security, or performance at 20 million rows. Those require running the actual application and a separate scale workload. The original challenge's 20B-parameter model ceiling still applies to the team's implementation.
