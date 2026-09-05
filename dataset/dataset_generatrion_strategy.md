Yes — for a small prototype, **10 banks + 1,000 accounts + 20,000 transactions** is a much better balance between realism, RAG testing, and easy local development.

 Tiby Dataset Generation Strategy — Prototype

# Tiby Finance Assistant — Dataset Generation Strategy
Dataset generation complete. All integrity checks pass:

bank.csv: 10 banks with required codes (HDFC, ICIC, SBIN, UTIB, KKBK, CNRB, UBIN, AUBL, TMBL, RATN)
account.csv: 1,000 accounts, 534 unique entities, balances (pos: 782, zero: 76, neg: 142), all bank_codes valid
transaction.csv: 20,000 transactions, all account_ids valid, credit/debit 40%/60%, reference_id 94.9% populated, UTR 70.9% populated

 ## Objective

 Generate a realistic, fully synthetic Indian banking dataset for testing Tiby's:

 - RAG retrieval
- Text-to-SQL
- Account/entity resolution
- Transaction search
- Aggregations and analytics
- Date, amount, merchant and reference filtering

 The CSVs are the **single source of truth**. RAG documents and evaluation questions should be derived from the same data.

 ## Dataset Size

 | File | Rows | Purpose |
| --- | --- | --- |
| `bank.csv` | 10 | Bank lookup |
| `account.csv` | 1,000 | Account/entity/balance queries |
| `transaction.csv` | 20,000 | Primary transaction/RAG workload |

 Relationship:

```
BANK 1 ──────── N ACCOUNT 1 ──────── N TRANSACTION
      bank_code             account_id
```

---

 ## 1\. `bank.csv`

 Schema:

```
bank_code,bank_name
```

 Rules:

 - Exactly 10 banks.
- `bank_code` is the primary key.
- Preserve: `HDFC`, `ICIC`, `SBIN`, `UTIB`, `KKBK`, `CNRB`, `UBIN`, `AUBL`, `TMBL`, `RATN`.
- Use canonical Indian bank names.
- No duplicate codes or names.

---

 ## 2\. `account.csv`

 Schema:

```
account_id,entity_id,account_number,program_id,available_balance,bank_code
```

 Rules:

 - Exactly 1,000 accounts.
- UUID `account_id`, always unique.
- Use \~700 unique entities so some entities own multiple accounts.
- Generate synthetic 12–16 digit account numbers.
- Programs: primarily `4`, `21`, `46`.
- Include positive, zero and negative balances.
- Every `bank_code` must exist in `bank.csv`.

 Use a mildly skewed bank distribution so some banks have more accounts than others.

---

 ## 3\. `transaction.csv`

 Schema:

```
transaction_id,account_id,transaction_date,transaction_type,description,transaction_amount,transaction_reference_id,utr_number
```

 Rules:

 - Exactly 20,000 transactions.
- UUID `transaction_id`, always unique.
- Every `account_id` must exist in `account.csv`.
- Dates: `2024-01-01` through `2026-09-05`.
- `transaction_type`: only `credit` or `debit`.
- Target distribution: \~40% credit / \~60% debit.
- Use realistic Indian banking formats: UPI, IMPS, NEFT, RTGS, FT, cheque, salary, EMI, utility, charges, loan disbursement, retail, etc.
- Include recurring merchants such as `SELECTION ELECTRONICS`, `SELECTRICITY TWO PRIVATE LIMITED`, `RELIANCE DIGITAL RETAIL LTD`, and `BAJAJ FINANCE LTD`.
- Use at least 100 synthetic merchants/business entities.
- `transaction_reference_id`: \~95% populated and searchable.
- `utr_number`: \~70% populated and treated as sensitive.

 ### Reference semantics

```
"reference number" → transaction_reference_id
"UTR"              → utr_number
```

 Do not treat the two fields as interchangeable.

---

 ## RAG & Query Diversity

 Even with only 20,000 transactions, deliberately include:

 - Multiple accounts per entity
- Accounts with many/few transactions
- Repeated merchants
- Repeated transaction amounts
- Multiple transactions on the same date
- Missing references/UTRs
- Large transactions
- Negative balances
- Transactions across different months/years
- Similar descriptions with different references

 The dataset should support questions such as:

```
What is my HDFC account balance?
Show debits above ₹50,000.
How much did I spend in June 2026?
Find transaction reference S5314253.
Find the transaction with this UTR.
Which merchant received the most money?
Which account has the highest balance?
```

---

 ## Data Integrity

 Before finalizing:

 - `bank_code` must be unique.
- `account_id` must be unique.
- `transaction_id` must be unique.
- Every account references an existing bank.
- Every transaction references an existing account.
- Zero orphan records.
- No invalid transaction types.
- Correct decimal/date formats.
- Nullable reference and UTR fields must remain valid CSV values.

---

 ## Reproducibility

 Use:

```
SEED = 20260905
```

 The same seed and configuration should produce the same dataset.

 ## Output

 Generate exactly:

```
bank.csv
account.csv
transaction.csv
```

 Use UTF-8, comma-delimited CSV with correct escaping and no extra columns.

 **Core principle:** Generate the relational dataset first, then derive RAG documents and evaluation questions from it. This ensures the database, RAG results, and expected SQL answers all share the same source of truth.