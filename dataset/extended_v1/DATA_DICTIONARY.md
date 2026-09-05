# Business data dictionary

Exact column types, primary keys and foreign keys are in `data_dictionary.json` and `schema.sql`. SQLite foreign keys must be enabled on each writing connection. Money columns ending in `_minor` store exact integer INR paise; never store binary floating-point monetary oracles.

| Table | Purpose and important fields |
|---|---|
| `organization` | One synthetic company and its INR convention; `provenance` documents that assumption. |
| `entity` | Preserves each original owner ID and maps it to the demo organization under `scope_basis`. |
| `bank` | Original bank codes and names; use bank code for alias resolution. |
| `account` | Original account ownership, bank, program and undated available balance. `balance_as_of` is NULL. Account numbers are strings and should be masked in user answers. |
| `bank_transaction` | Original plus new bank records. `amount_minor` is signed; `transaction_type` agrees with its sign. Date-only `transaction_date`, non-unique nullable `reference_id`, nullable sensitive `utr_number`, and explicit `provenance`. |
| `chart_of_accounts` | Small synthetic reporting category list, not a complete double-entry general ledger. SUP, TECH, LOG, UTIL, SERV, FEES, XFER, UNCLASS. |
| `vendor` | Canonical synthetic vendor identity, display name and default reporting category. |
| `vendor_alias` | Alias-to-vendor mappings. An alias can intentionally resolve to multiple vendors; `ACME` is ambiguous. |
| `cash_allocation` | Splits each bank transaction into cash-reporting pieces. All pieces sum exactly to that transaction's signed amount. `vendor_id` is NULL for fees, transfers and unclassified rows. |
| `invoice` | Expected vendor obligation, invoice/due dates and positive face amount. Status is derived from payment allocations, not stored redundantly. |
| `payout` | One logical instruction with invoice, vendor, account, dates, positive amount and operational status. Status is as of 2026-09-05, not a complete lifecycle history. |
| `payout_attempt` | Attempts under a logical payout. Never sum attempts as additional payouts. Includes attempt number, date, status and optional failure code. |
| `payout_transaction` | Many-to-many links between instructions and bank rows. `role` is settlement, reversal or suspected_duplicate. These are evidence links, not numeric allocations; summing joined bank amounts can double-count batch settlements. |
| `invoice_allocation` | Signed amounts applied to an invoice under a payout. Positive reduces outstanding; a reversal's negative allocation reopens it. Commercial refunds follow the explicit refund policy below. |
| `refund` | Positive refund amount, credit transaction, original payout and reason. `reopens_invoice=1` for a bank reversal, 0 for the commercial refund fixture. |
| `expected_entry` | Independent expected settlement record: expected date, account, optional payout, positive magnitude and debit/credit direction. It is the comparison side for bank reconciliation. |
| `reconciliation_case` | Synthetic adjudication status, reason, review note and as-of date. Not inferred from missing identifiers. |
| `case_expected` | Expected entries in a case. An expected entry belongs to one case. |
| `case_observed` | Bank transactions in a case. A bank transaction belongs to one case; unassigned transactions remain not assessed. |
| `reconciliation_match` | Confirmed positive allocations between expected entries and observed bank rows. Supports one-to-many and many-to-one. An ambiguous case intentionally has no arbitrarily chosen matches. |
| `internal_transfer` | Links debit and credit on two demo-owned accounts; both legs are excluded from vendor spend and net to zero at company scope. |
| `anomaly_label` | Transparent synthetic anomaly rule, target payout, and explicit baseline IDs; not a fraud probability. |
| `scenario` | Human-readable business scenario with record IDs and intended behavior. |

## Cash-allocation kinds

| Kind | Meaning |
|---|---|
| vendor_payment | Observed supplier-related debit under the synthetic classification. Includes disputed debits; filter reconciliation status explicitly if requested. |
| vendor_refund | Linked vendor return/reversal credit. |
| bank_fee | Separately recognized bank charge, including a fee bundled with a payout debit. |
| internal_transfer | Movement between two accounts mapped to this company. |
| unclassified | Original cash movement without an invented vendor/business classification. |

## Payout statuses

| Status | Meaning |
|---|---|
| succeeded | Operational system reports settlement; bank evidence may still be missing or discrepant. |
| pending | Awaiting processing; no posted bank settlement in its fixture. |
| processing | In progress; ambiguous candidate debits are not silently assigned to it. |
| failed | Logical instruction failed; do not count as paid cash without bank evidence. |
| cancelled | Instruction cancelled before settlement. |
| reversed | A previously posted payout was fully reversed; both bank legs remain visible. |

## Reconciliation statuses

| Status | Meaning |
|---|---|
| reconciled | All case expected and observed amounts are fully matched, with account and direction checks. |
| unreconciled | Missing expected/observed counterpart: missing_bank_record or unexpected_bank_debit. |
| discrepancy | A known mismatch, here an amount shortfall with partial matching. |
| ambiguous | More than one plausible allocation or a possible duplicate; manual evidence needed. |
| not_due | Future settlement expectation as of September 5; not yet an overdue failure. |
| not_assessed | Derived transaction-level state for rows without a case; not stored as a case status. |

For the broad question “Which transactions are still unreconciled?”, the golden set returns actionable transaction rows from unreconciled, discrepancy and ambiguous cases, with their precise status shown. A missing-bank case has no transaction to list, so case-level evidence must also be shown. `not_assessed` and `not_due` are reported separately.

## Safe views and joins

- `v_cashflow`: transaction allocations with vendor/category labels and positive inflow/outflow fields. Use this for category/vendor cash totals. Group monetary allocations; count distinct transaction IDs.
- `v_invoice_balance`: face amount, paid amount and outstanding amount. Overdue means outstanding > 0 and due_date < 2026-09-05. An installment payout must not cause the invoice face value to be counted twice.
- `v_reconciliation`: expected, observed and matched magnitudes per case, calculated separately to avoid join multiplication. A case holds comparable directions; a reversal credit has its own case.
- `v_transaction_reconciliation`: explicit or derived status for every bank row.

Do not join invoices → payouts → transactions → matches and sum the raw amounts in one flat query: batch, split, retry and fee fixtures deliberately reveal double counting. Aggregate each relevant grain first or use the views.

## Privacy and missing-data semantics

This is synthetic data, but still exercise masked account display and avoid echoing UTRs unnecessarily. A reference is not a unique key. Empty original identifier cells map to SQL NULL. Absence of records before 2024-01-01 is a coverage limitation, not evidence of no real activity. There is no tax model, FX table, historical balance series or intraday clock.
