# Problem-statement coverage

| Requirement | Data and cases supplied | What the application must still do |
|---|---|---|
| Plain-language queries | 80 questions across dates, vendors, categories, statuses, lookups and ambiguity | Interpret questions and validate typed filters |
| Spend | Signed bank cash, split fee allocations, vendor/category mappings, refunds and transfers | Use agreed cash definitions and disclose unclassified coverage |
| Vendor payouts | Invoices, logical instructions, retries, pending/processing/failed/cancelled/succeeded/reversed statuses | Choose instruction versus observed cash metric correctly |
| Reconciliation | Independent expected entries, bank evidence, cases and match allocations | Return exact status and evidence; do not invent matches |
| Correct filtering and computation | Integer paise, month boundaries, 50,000 versus 50,000.01 threshold fixtures, exact 1.01 amount | Calculate in database, not in model prose |
| Verifiable answers | Reference SQL, exact expected outputs, evidence SQL and record IDs | Present record drill-down or breakdown table |
| Hallucination guardrails | Unsupported tax/FX/forecast/history/time queries; ambiguous aliases; no-record cases | Ask clarification or state the exact limitation |
| Multi-turn context | August total → July total → increase, with prior-case IDs and frozen date | Preserve metric/vendor/scope during follow-ups |
| Explainability | Explicit case reason codes, matching allocations and cash allocation views | Explain the calculation and its evidence without inventing reasoning |
| CSV export | All database tables and golden cases supplied as CSV | Export filtered answer breakdowns from UI |
| Confidence signals | Clear distinction among reconciled, discrepant, ambiguous, not assessed and unsupported | Express evidence limitations, not fabricated confidence percentages |
| Anomaly callout | Six fixed historical payouts and one 25x-median outlier with a visible 5x rule | Answer the original question and label the heuristic caveat |
| Model choice and efficiency | Stable evaluation set for comparison | Run <=20B-parameter model; report actual accuracy, latency and usage |
| Scale to 20M records | Small functional fixture: 20,036 bank rows, indexed queryable schema | Generate and measure a separate large workload; no scale test is claimed here |
| Single company/currency | Explicit synthetic organization mapping and INR convention | Keep those conventions visible and consistent |
| Architecture/demo/submission | Join documentation, SQL schema, demo questions and evaluation artifacts | Build chat/backend, architecture diagram and presentation |

## Curated scenarios

1. Exact vendor settlement.
2. Previous-month comparison.
3. First day of month included.
4. Last day of month included.
5. Next month excluded.
6. Exactly-at-threshold debit.
7. One-paise-above-threshold debit.
8. Exact decimal arithmetic.
9. Reconciled payment with missing reference.
10. Pending payout.
11. Processing payout.
12. Failed payout.
13. Cancelled payout.
14. Failed attempt followed by successful retry.
15. Partially paid invoice.
16. Overdue unpaid invoice.
17. Multiple installments clearing one invoice.
18. Multiple payouts in one bank debit.
19. One payout in multiple bank debits.
20. Vendor amount plus bundled bank fee.
21. Amount mismatch despite operational success.
22. Expected payout with no bank row.
23. Unexpected bank debit with no ledger expectation.
24. Possible duplicate payment needing review.
25. Same-amount candidate ambiguity.
26. Ledger/bank timing across month boundary.
27. Full reversal reopening invoice.
28. Partial commercial refund not reopening invoice.
29. Own-account transfer.
30. Large payout against explicit historical baseline.
31. Ambiguous vendor nickname.
32. Legacy unassessed transactions.

Additional questions exercise original reference collisions, missing UTRs, bank grouping, overdue expectations, unsupported data and attempted fabricated answers. `fixtures.json` contains direct record IDs for the curated scenarios.

## Limits of “all cases”

The problem statement does not enumerate every possible financial question. This package supplies its requested data domains and representative positive, edge, ambiguous and unsupported cases. It does not invent data for tax, FX, historical balances or forecasts to pretend universal support. Synthetic categories cover only selected original transactions; the remaining legacy movements deliberately preserve the uncertainty visible in the source.
