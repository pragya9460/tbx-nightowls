# Demo questions and verified results

As of 2026-09-05; all demo-company accounts; currency INR. Money values below ending in `_minor` are exact paise. Divide by 100 for display. Vendor cash spend excludes unclassified legacy cash.

## G001 ? How much did we spend on vendor payouts last month?

Defined as observed gross vendor cash outflow, excluding fees, transfers and unclassified movements. Includes disputed cash; status-filtered payouts are a separate metric.

```json
[
  {
    "transaction_count": 39,
    "amount_minor": 477958904
  }
]
```

## G002 ? And the month before?



```json
[
  {
    "transaction_count": 27,
    "amount_minor": 578501006
  }
]
```

## G003 ? How much did that increase from July to August?



```json
[
  {
    "increase_minor": -100542102
  }
]
```

## G006 ? What was net cash movement in August?



```json
[
  {
    "transaction_count": 680,
    "amount_minor": -3003130899
  }
]
```

## G007 ? What was net vendor cash spend in August?



```json
[
  {
    "transaction_count": 41,
    "amount_minor": 477588904
  }
]
```

## G018 ? Break August vendor outflow down by category.



```json
[
  {
    "category_code": "LOG",
    "amount_minor": 36515849
  },
  {
    "category_code": "SERV",
    "amount_minor": 45183010
  },
  {
    "category_code": "SUP",
    "amount_minor": 372143654
  },
  {
    "category_code": "TECH",
    "amount_minor": 11905620
  },
  {
    "category_code": "UTIL",
    "amount_minor": 12210771
  }
]
```

## G029 ? Which payouts succeeded after a failed attempt?



```json
[
  {
    "payout_id": "PAY-RETRY",
    "amount_minor": 150000
  }
]
```

## G035 ? Which transactions are still unreconciled?

Includes actionable unmatched, discrepant and ambiguous cases. not_assessed is separate; missing-bank cases have no bank transaction and are shown in case evidence.

```json
[
  {
    "transaction_id": "5eea1f24-4538-548a-960f-5b60049822a6",
    "reconciliation_status": "discrepancy",
    "case_id": "REC-MISMATCH",
    "reason_code": "amount_mismatch"
  },
  {
    "transaction_id": "86555dea-681b-5b03-be6b-e1d064618a13",
    "reconciliation_status": "ambiguous",
    "case_id": "REC-DUPLICATE",
    "reason_code": "possible_duplicate"
  },
  {
    "transaction_id": "ac34a0ed-c0d5-5798-b9f1-21f85f5402e7",
    "reconciliation_status": "unreconciled",
    "case_id": "REC-UNEXPECTED",
    "reason_code": "unexpected_bank_debit"
  },
  {
    "transaction_id": "de2ddf15-a3c5-5119-802d-ccdf7644ce91",
    "reconciliation_status": "ambiguous",
    "case_id": "REC-DUPLICATE",
    "reason_code": "possible_duplicate"
  },
  {
    "transaction_id": "ec28503b-e03f-570e-aa5f-e20508390a7d",
    "reconciliation_status": "ambiguous",
    "case_id": "REC-AMBIG",
    "reason_code": "multiple_candidates"
  }
]
```

## G060 ? What was vendor cash outflow in September 1 through 5 inclusive?



```json
[
  {
    "transaction_count": 4,
    "amount_minor": 29458593
  }
]
```

For an interactive demo, follow the first three questions with a vendor/category breakdown, then explain REC-FEE, REC-MISMATCH and REC-DUPLICATE. End with an unsupported historical-balance query to demonstrate grounding.
