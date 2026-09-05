# Artha — a Finance Assistant That Actually Understands You

## Extended finance dataset and golden answers

The original 20,000-transaction CSV dataset is in [`dataset/`](dataset/). The [extended dataset](dataset/extended_v1/README.md) adds explicitly synthetic vendors, invoices, payouts, and reconciliation scenarios, plus 80 golden evaluation questions.

- [SQLite database](dataset/extended_v1/finance.sqlite) and [CSV tables](dataset/extended_v1/csv/)
- [80 human-facing questions and expected answers](dataset/extended_v1/human_answers/golden_human_answers.md)
- [Machine-readable golden cases](dataset/extended_v1/golden_cases.json)
- [Data dictionary](dataset/extended_v1/DATA_DICTIONARY.md) and [coverage and limits](dataset/extended_v1/COVERAGE.md)

Run `python dataset/extended_v1/validate.py` to validate the package. It passed 171 checks and 65 reference-query replays. These results do not measure assistant accuracy or 20-million-row performance.

The extended dataset is a standalone evaluation fixture. The current application described below still uses its existing bank/account/transaction model and loader; this merge does not add backend support for the extension's vendor, invoice, payout or reconciliation tables. Its golden answers apply only when querying the extended fixture under its documented conventions.


**TBX × BVP Tech Catalyst Hackathon.** Artha is a finance Q&A assistant over
real bank data where the LLM's only job is to map a natural-language question
to a **validated structured query**. Every financial number is computed
deterministically by the backend query engine against MySQL — the LLM never
executes SQL, never computes values, and never restates them.

```
question → lightweight LLM (or rule-based fallback) → structured Finance Query
        → Pydantic validation → deterministic query builder → MySQL
        → computed result → evidence builder → template answer
        → Answer + "How I got this" evidence
```

If the data doesn't support an answer, Artha **refuses with a structured
reason** instead of guessing. That refusal behaviour is the core thesis:

> The assistant understands financial context instead of simply matching
> keywords, while refusing to guess when the data does not support an answer.

---

## Contents

1. [What was implemented](#what-was-implemented)
2. [Verified end-to-end examples](#verified-end-to-end-examples)
3. [Quick start](#quick-start)
4. [Supported question categories](#supported-question-categories)
5. [Architecture](#architecture)
6. [Grounding & evidence](#grounding--evidence)
7. [Hallucination guardrails](#hallucination-guardrails)
8. [Multi-turn conversation](#multi-turn-conversation)
9. [Date handling](#date-handling)
10. [Sensitive data handling](#sensitive-data-handling)
11. [Tests & evaluation](#tests--evaluation)
12. [Model efficiency](#model-efficiency)
13. [Scaling toward 20M records](#scaling-toward-20m-records)
14. [Current hackathon scope vs the future Financial Twin](#current-hackathon-scope-vs-the-future-financial-twin)
15. [Assumptions](#assumptions)
16. [Intentionally not implemented](#intentionally-not-implemented)

---

## What was implemented

**Backend** (`backend/`, Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2):

- `app/schemas/query.py` — the semantic layer. `FinancialQuery` with closed
  enum allowlists for intent, metric, aggregation, group-by, sort, and date
  ranges; `extra="forbid"` rejects invented fields; filter values are
  validated against SQL-token patterns; cross-field validators enforce
  coherence (balance intents require the balance metric, comparisons require
  a comparison spec, …).
- `app/query_engine/` — deterministic engine over the **actual TBX schema**
  (`bank`, `account`, `transaction`). Joins live in exactly one place;
  account/balance questions read `account.available_balance` (no fabricated
  balances from transaction sums); transaction questions aggregate in SQL.
  Sensitive fields (`account_number`, `utr_number`) are masked **at the
  engine boundary** so nothing downstream can leak them.
- `app/llm/provider.py` — provider abstraction with two implementations:
  `AnthropicProvider` (structured outputs via the Messages API with a JSON
  schema constrained to the same allowlists) and `RuleBasedProvider`, a
  deterministic regex baseline so the demo works without an API key.
- `app/conversation/memory.py` — structured multi-turn memory (last intent /
  metric / transaction type / bank / date range / filters) — not a transcript
  dump. Powers "What about July?" and "How does that compare?".
- `app/services/` — deterministic date resolution (fixed IST clock), seed
  generation, evidence building, template-based answer generation with
  Indian digit grouping (₹1,24,850).
- `app/api/routes.py` — REST API: `/api/chat`, `/api/query`, `/api/health`.
- `tests/` — **103 pytest tests** (engine, guardrails, understanding, API
  grounding) on SQLite in-memory — no services needed to run the suite.

**Frontend** (`frontend/`, React 19 + TypeScript + Vite + Tailwind v4):
chat UI that visually separates **ANSWER** (the big number + sentence),
**"✓ Grounded — view how this was calculated"** (date range, filter,
calculation, records matched), and **SOURCE RECORDS** (masked table).

**Evaluation** (`evaluation/`): 31-case benchmark (balance, bank lookup,
debit/credit spend, dates, aggregation, grouping, sorting, description
search, reference vs UTR, multi-turn, unsupported) — accuracy is **computed
from actual execution**, never asserted. `run_eval.py` also records latency
per provider/model.

**Infra**: `docker-compose.yml` (MySQL 8.4 + backend + nginx-served
frontend), `Makefile`, `.env.example`.

## Verified end-to-end examples

| Question | Answer |
|---|---|
| "What is my total available balance?" | Total available balance across N accounts: ₹X. |
| "Which bank holds the most money?" | `<Bank>` holds the most: ₹X across N accounts. |
| "How much did I spend last month?" | You spent ₹X in `<Month>` across N **debit** transactions. |
| "How much money came in last month?" | You received ₹X across N credit transactions. |
| "Show transactions above ₹50,000." | Found N transactions above ₹50,000… + records table |
| "What did I spend at Selection Electronics?" | You spent ₹X containing "SELECTION ELECTRONICS"… |
| "Find transaction reference 1715499972." | Found 1 matching transaction… (exact match on `transaction_reference_id`) |
| "Find UTR xyz…" | Matched on `utr_number` — never conflated with reference id |
| "Which invoices are overdue?" | **Refusal**: invoice data is not available in the current dataset. |
| "What about July?" (follow-up) | Same metric + type, new month. |

---

## Quick start

### Option A — Docker (everything in one command)

```bash
docker compose up --build -d
# UI:      http://localhost:5173
# API:     http://localhost:8000/api/health
```

First boot generates the seed data (10 banks, 25 accounts, 8,000
transactions, seed=42) and loads it into MySQL automatically. Set
`ANTHROPIC_API_KEY` in `.env` (copy `.env.example`) to enable LLM query
understanding; without it the deterministic rule-based provider is used and
all core questions still work.

### Option B — local development

```bash
# 1. MySQL (any MySQL 8+ with user/pass/db = artha/artha/artha works)
docker run -d --name artha-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=artha -e MYSQL_USER=artha \
  -e MYSQL_PASSWORD=artha -e MYSQL_DATABASE=artha mysql:8.4

# 2. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a; source ../.env; set +a                 # load repository-root config
python scripts/load_data.py --generate --drop   # seed the database
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api → :8000
```

Makefile shortcuts: `make docker-up`, `make install`, `make seed`, `make test`,
`make eval`, `make api`, `make frontend`.

---

## Supported question categories

| Category | Examples | Intent |
|---|---|---|
| Account intelligence | "What is my available balance?" · "Total balance across all accounts?" · "Which account has the highest balance?" · "Show me all my accounts." | `account_balance` / `account_list` |
| Bank intelligence | "Which bank holds the most money?" · "How much money do I have in HDFC?" · "How many accounts with each bank?" | `bank_balance` / `bank_account_count` |
| Debit spend / credit inflow | "How much did I spend last month?" · "How much came in this week?" | `transaction_summary` |
| Date-filtered aggregation | "in June" · "between Jun 1 and Jun 30" · "last 7 days" · "this year" | any transaction intent |
| Largest / threshold lists | "Show my largest transactions" · "Show transactions above ₹50,000" | `transaction_list` |
| Description search | "spend at Selection Electronics" · "transactions containing Reliance" | filters.`description_contains` |
| Monthly peak / trend | "Which month had the highest debit amount?" | `monthly_trend` |
| Top descriptions | "Top transaction descriptions by spend" | `top_descriptions` |
| Reference search | "Find transaction reference S5314253" · "Find UTR xyz…" | `reference_lookup` |
| Comparisons (multi-turn) | "How does that compare with the month before?" | `comparison` |

`supported_capabilities()` in `app/schemas/query.py` is the single source of
truth — surfaced in refusals and covered by tests.

---

## Architecture

```
backend/
├── app/
│   ├── config.py                  # env settings (provider, model, DB URL)
│   ├── db.py                      # SQLAlchemy engine/session factory
│   ├── models/entities.py         # Bank, Account, Transaction (TBX schema)
│   ├── schemas/query.py           # ★ the semantic layer (FinancialQuery + refusals)
│   ├── llm/provider.py            # LLM abstraction (Anthropic | rule-based)
│   ├── query_engine/              # ★ deterministic engine (builder + engine + evidence)
│   ├── conversation/memory.py     # structured multi-turn context
│   ├── services/                  # seed data, answers (templates), chat service
│   └── api/                       # FastAPI routes + request/response schemas
├── scripts/load_data.py           # CSV ingestion (synthetic seed OR official data)
└── tests/                         # pytest: 103 tests, SQLite in-memory

frontend/                          # React chat UI with grounding panel
evaluation/                        # benchmark.json + run_eval.py + results.json
```

### Division of responsibility (the core design rule)

| Concern | Owner |
|---|---|
| Understanding the question | Lightweight LLM (or rule-based fallback) — emits a structured draft |
| Deciding whether a question is supported | Pydantic validators + explicit refusal taxonomy |
| Resolving "last month" to dates | Backend (`resolve_date_range(today, …)` — deterministic, never trusts the LLM's dates) |
| Computing every financial value | SQL via the query engine |
| Writing the answer | Deterministic templates over computed values |
| Masking sensitive values | Engine boundary — before any data leaves the DB layer |

---

## Grounding & evidence

Every grounded answer carries evidence, rendered in the UI as three distinct
zones — **ANSWER**, **HOW I GOT THIS**, and **SOURCE RECORDS**:

```json
{
  "how_calculated": {
    "date_range": "Aug 2026",
    "operation": "SUM(transaction_amount)",
    "records_matched": 28,
    "filters": {"transaction_type": "debit"}
  },
  "source": "MySQL — TBX financial dataset (bank / account / transaction, deterministic query engine)",
  "grounded": true,
  "records": [ ... up to 15 masked rows ... ]
}
```

Comparisons include the second period's evidence, so both sides of the
percentage are auditable. Large listings are summarized (true count) with a
capped record sample — never a 2,000-row dump.

## Hallucination guardrails

- **No SQL from the LLM.** The LLM emits a closed-allowlist JSON object;
  there is no path from model output to raw SQL.
- **No numbers from the LLM.** All amounts/counts/percentages come from SQL;
  answers are rendered by templates.
- **No fabricated domains.** Payroll, taxes, invoices, vendors, escrow,
  customers, profit, forecasts → structured refusal naming the missing data.
- **Explicit interpretation.** "How much did I spend?" maps to debit
  transactions and the *answer says so* ("You spent ₹X … across N **debit**
  transactions").
- **Reference vs UTR are distinct.** A bare "reference number" hits
  `transaction_reference_id` (plaintext); only an explicit "UTR" hits
  `utr_number` — never silently interchangeable.
- **Re-validation after the LLM.** Draft JSON must pass
  `FinancialQuery.model_validate` regardless of provider.
- **Deterministic dates.** The LLM names a month or range *type*; the
  backend computes the actual dates.

## Multi-turn conversation

Structured memory (intent, metric, transaction type, bank, date range) — not
a transcript:

- "How much did I spend in August?" → debit summary for Aug.
- "What about July?" → same metric + type, new month.
- "Which bank contributed the most?" → inherits the debit type, groups by bank.
- "How does that compare with the month before?" → comparison intent against
  the previous answer's period.

## Date handling

Supported: today, yesterday, this/last week, this/last month, month before,
last N days/months, named months ("June", "August 2026"), explicit ranges
("between 2026-06-01 and 2026-06-30"), this/last year, all time.

All resolution is deterministic: `resolve_date_range(today, spec)` takes
`today` explicitly (reproducible tests/evals) and computes absolute dates
server-side. **Timezone assumption: Asia/Kolkata (IST)** — the dataset is
Indian banking data; "today"/"this month" resolve in IST regardless of
server locale.

## Sensitive data handling

- `account_number` → masked to `XXXXX1234` in every API response, evidence
  row, and answer (enforced by tests, applied at the engine boundary).
- `utr_number` → truncated/masked the same way; lookups by UTR work (the DB
  sees the full value) but displays are masked.
- Never included in LLM prompts; never logged.
- All filter values are parameter-bound (no string interpolation) and
  additionally screened for SQL-token patterns; there is no text-to-SQL path.
- The schema contract is respected: `transaction_reference_id` is searchable
  plaintext, and the system never pretends otherwise.

## Tests & evaluation

```bash
cd backend && .venv/bin/python -m pytest -q     # 103 tests
python evaluation/run_eval.py --provider rule_based
python evaluation/run_eval.py --provider anthropic   # needs ANTHROPIC_API_KEY
```

- **103/103 tests pass** — parsing, DSL validation, SQL generation, date
  resolution, aggregation, grounding (answers verified against independent
  ORM-computed expected values), sensitive-field masking, unsupported and
  ambiguous questions, multi-turn context, SQL-injection resistance, and
  large-result capping.
- Evaluation: 31 cases; accuracy **computed from execution** (30/31 → 1.0
  after fixes), per-case latency recorded.

## Model efficiency

The LLM layer is swappable (`build_provider`), the model is env-configurable
(`ARTHA_MODEL`), and the rule-based provider demonstrates the whole pipeline
with **zero model calls**. Default: `claude-haiku-4-5` — chosen as the
smallest model that reliably produces structured JSON; `run_eval.py` records
latency per provider/model so the final choice can be justified with data,
and any provider (local, API, mock) can be added by subclassing
`LLMProvider`.

## Scaling toward 20M records

The engine emits a fixed family of parameterized SELECTs — filtering,
aggregation, grouping, sorting, and LIMIT all happen in the database; Python
receives only the result rows and a capped evidence sample. Indexes match
actual query patterns (`transaction_date`, `account_id`,
`transaction_type`, `transaction_reference_id`, composite
`(transaction_date, transaction_type)` and `(account_id, transaction_date)`)
— nothing speculative. For 20M rows the next steps (intentionally out of
scope) would be monthly partitioning, keyset pagination for lists, and
materialized month summaries; the API contract would not change.

## Current hackathon scope vs the future Financial Twin

**Current implementation** (grounded in the provided dataset only):

```text
Bank → Account → Transaction
```

**Future Financial Twin** (architecturally anticipated via the provider /
semantic-layer seams, but NOT present in the current data model and never
mixed into grounded answers):

```text
Accounts · Transactions · Vendors · Invoices · Receivables · Payables
Mandates · Documents · Restrictions · Approvals · Preferences
```

The semantic layer makes this split structural: every intent is allowlisted,
so a question that would require Financial-Twin data (vendors, invoices,
mandates) is *refused with the missing domain named* rather than answered
from invented tables.

## Assumptions

- **Currency INR**, formatted with Indian digit grouping (₹1,24,850).
- **Timezone IST** for all relative-date resolution (see Date handling).
- `available_balance` is the current balance snapshot per account; balance
  questions read it directly — they do NOT reconstruct balances by summing
  transactions (the data doesn't support opening balances).
- The synthetic seed (10 banks from the schema's canonical list, 25
  accounts, 8,000 transactions over 12 months) exists so every question type
  has plausible data before the official dataset is loaded; it is not a
  performance benchmark.
- Conversation memory is in-process (per API worker). For multi-worker
  deployments it would move to MySQL/Redis; the interface wouldn't change.
- Seed data uses realistic description formats (NEFT/IMPS/UPI/FT with
  counterparty names) so description search behaves like it will on the
  official data.

## Intentionally not implemented

Per the hackathon scope:

- **Text-to-SQL** — the LLM never writes SQL; it fills a schema.
- **RAG / vector DB / embeddings** — not needed when answers come from
  parameterized queries.
- **Financial Twin tables** (vendors, invoices, mandates, approvals, …) —
  no fake domain tables; refusals name them instead.
- **Multi-agent orchestration** — one structured-understanding call +
  deterministic pipeline covers all supported questions.
- **Authentication / multi-tenancy** — prototype scope; sensitive-value
  masking is in, auth is not.
- **Hardcoded demo answers** — everything is computed live.
- **Write operations** — the assistant is read-only by design.
