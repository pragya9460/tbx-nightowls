# Artha — AI Finance Assistant

A finance Q&A assistant where the LLM's only job is to map a natural-language
question to a **validated structured query**. Every financial number is
computed deterministically by the backend query engine against PostgreSQL —
the LLM never executes SQL, never computes values, and never restates them.

```
question → LLM query understanding → structured FinancialQuery (Pydantic, allowlisted)
        → backend validation → deterministic query engine → PostgreSQL
        → evidence builder → template-based answer
```

If a question can't be mapped to something the semantic layer supports, the
assistant **refuses with a structured reason** (unsupported / ambiguous /
invalid) instead of guessing.

---

## Contents

1. [What was implemented](#what-was-implemented)
2. [Quick start](#quick-start)
3. [Supported question types](#supported-question-types)
4. [Architecture](#architecture)
5. [How it works, end to end](#how-it-works-end-to-end)
6. [Hallucination guardrails](#hallucination-guardrails)
7. [Evidence system](#evidence-system)
8. [Database & seed data](#database--seed-data)
9. [API reference](#api-reference)
10. [Tests & evaluation](#tests--evaluation)
11. [Swapping in the official dataset](#swapping-in-the-official-dataset)
12. [Scaling toward 20M records](#scaling-toward-20m-records)
13. [Assumptions](#assumptions)
14. [Intentionally not implemented](#intentionally-not-implemented)

---

## What was implemented

**Backend** (`backend/`, Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2):

- `app/schemas/query.py` — the semantic layer. `FinancialQuery` with closed
  enum allowlists for intent, metric, aggregation, group-by dimensions, and
  date-range types; `extra="forbid"` rejects unknown fields; cross-field
  validators enforce coherence (e.g. `top_vendors` requires
  `group_by=["vendor"]`, payout intents require payout metrics).
- `app/query_engine/` — deterministic engine. All joins live in one place
  (`builder._base_query`); `engine.execute()` returns typed `QueryResult`
  (summary / breakdown / records / metadata) and always reports the true
  matched-record count pre-grouping and pre-limit.
- `app/llm/provider.py` — provider abstraction with two implementations:
  `AnthropicProvider` (structured outputs via the Messages API with a JSON
  schema, `claude-haiku-4-5` by default) and `RuleBasedProvider`, a fully
  deterministic regex baseline so the demo works without an API key.
- `app/services/` — synthetic seed generation (seed=42, reproducible),
  date-range resolution, evidence building, and template-based answer
  generation (INR lakh/crore formatting).
- `app/conversation/memory.py` — structured multi-turn memory
  (last intent/metric/vendor/date range/filters) that powers follow-ups like
  *"How does that compare with the month before?"* — not a transcript dump.
- `app/api/routes.py` — REST API: `/api/chat`, `/api/query`, `/api/health`.
- `tests/` — 37 pytest tests across query engine, guardrails, understanding,
  and API grounding (run on SQLite in-memory; no services needed).

**Frontend** (`frontend/`, React 19 + TypeScript + Vite + Tailwind v4):
chat UI with suggested questions, loading/refusal/error states, clickable
suggestion chips on refusals, and an expandable **"✓ Grounded — view how this
was calculated"** evidence panel on every grounded answer.

**Evaluation** (`evaluation/`): 11-case benchmark with expected intents, date
ranges, and refusal reasons; runner scores accuracy + latency per provider
and writes `results.json`.

**Infra**: `docker-compose.yml` (Postgres 16 + backend + nginx-served
frontend), `Makefile`, `.env.example`.

Verified end-to-end against the Docker stack:

| Question | Answer |
|---|---|
| "How much did we spend on vendor payouts last month?" | You spent ₹55.76 lakh on vendor payouts in Aug 2026 across 139 payouts. |
| "How does that compare with the month before?" (follow-up) | ₹55.76 lakh for Aug 2026 vs ₹54.45 lakh for Jul 2026 — that's up 2.4%. |
| "How much did we pay ABC Suppliers last month?" | You paid ABC Suppliers ₹84,916 in Aug 2026 across 2 payouts. |
| "Which vendors received the most money last month?" | Total payouts for Aug 2026: ₹55.76 lakh. Top vendors were Nova Print Media (₹7.89 lakh), … |
| "Which transactions are still unreconciled?" | Found 135 unreconciled transactions for Aug 2026. Showing the 50 most recent. |
| "How many transactions were there last month?" | 690 transactions in Aug 2026. |
| "How much do we spend on salaries?" | **Refusal** (`unsupported_metric`): employee payroll data is not available in the current financial dataset. |
| "How much did we spend last month?" | **Refusal** (`ambiguous`): spend on vendor payouts or transactions? — with clickable suggestions. |

---

## Quick start

### Option A — Docker (everything in one command)

```bash
docker compose up --build -d
# UI:      http://localhost:5173
# API:     http://localhost:8000/api/health
```

First boot generates the seed data and loads it into Postgres automatically.
Set `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`) to enable LLM query
understanding; without it the deterministic rule-based provider is used and
all core questions still work.

### Option B — local development

```bash
# 1. Postgres (any instance with user/pass/db = artha/artha/artha works)
docker run -d --name artha-pg -p 5432:5432 \
  -e POSTGRES_USER=artha -e POSTGRES_PASSWORD=artha -e POSTGRES_DB=artha \
  postgres:16-alpine

# 2. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
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

## Supported question types

| Type | Examples | Backend intent |
|---|---|---|
| Vendor payout totals | "How much did we spend on vendor payouts last month?" | `vendor_payout_summary` |
| Unreconciled lists/counts | "Which transactions are still unreconciled?" / "How many were unreconciled last month?" | `unreconciled_list` / `transaction_count` |
| Vendor-specific spend | "How much did we pay ABC Suppliers last month?" | `vendor_spend` |
| Top vendors | "Which vendors received the most money last month?" | `top_vendors` (group by vendor, top N) |
| Comparisons | "How does that compare with the month before?" | `comparison` + `ComparisonSpec` |

The full allowlist is introspectable at runtime: `supported_capabilities()`
in `app/schemas/query.py` is the single source of truth, surfaced to users in
refusal messages and covered by the schema in `app/llm/provider.py`.

---

## Architecture

```
backend/
├── app/
│   ├── config.py                  # env-driven settings (provider, model, DB URL)
│   ├── db.py                      # SQLAlchemy engine/session factory
│   ├── models/entities.py         # Vendor, Transaction, VendorPayout, Reconciliation
│   ├── schemas/query.py           # ★ the semantic layer (FinancialQuery + refusals)
│   ├── llm/provider.py            # LLM provider abstraction (Anthropic | rule-based)
│   ├── query_engine/              # ★ deterministic engine (builder + engine + evidence)
│   ├── conversation/memory.py     # structured multi-turn context
│   ├── services/                  # seed data, answers (templates), chat service
│   └── api/                       # FastAPI routes + request/response schemas
├── scripts/load_data.py           # CSV ingestion (seed OR official dataset)
└── tests/                         # pytest: engine, guardrails, understanding, grounding

frontend/
├── src/App.tsx                    # chat UI
├── src/components/EvidenceTable.tsx  # evidence panel + data tables
└── src/types.ts                   # API contract types

evaluation/                        # benchmark.json + run_eval.py + results.json
```

### Division of responsibility (the core design rule)

| Concern | Owner |
|---|---|
| Understanding the question | LLM (or rule-based fallback) — emits a structured draft |
| Deciding whether a question is supported | Pydantic schema validators + explicit refusal taxonomy |
| Resolving "last month" to dates | Backend (`resolve_date_range(today, …)` — deterministic, testable, never trusts the LLM's dates) |
| Computing every financial value | SQL via the query engine |
| Writing the answer | Deterministic templates over computed values |
| Grounding/Evidence | Evidence builder — same transaction that produced the answer |

---

## How it works, end to end

1. **Understand** — `POST /api/chat` passes the question + conversation id to
   the active provider. The Anthropic provider uses structured outputs (JSON
   schema on the response) constrained to the same allowlists the validator
   enforces; the rule-based provider does regex mapping.
2. **Refuse or proceed** — unsupported domain (salaries, taxes, revenue),
   ambiguous subject, or invalid structure → structured refusal with
   suggestions; no database call is made.
3. **Resolve dates** — relative ranges become absolute dates server-side.
   `today` is an explicit argument so evaluation is reproducible.
4. **Validate** — `FinancialQuery.model_validate` re-checks everything the
   LLM emitted. Anything outside the allowlist raises; the request never
   reaches SQL otherwise.
5. **Execute** — the engine builds one of a fixed family of SELECTs
   (summary, grouped, list) with the allowlisted filters, and computes the
   true matched-record count before grouping/limiting.
6. **Compare (if asked)** — a deep copy of the validated query with the
   previous period's dates is executed separately; percentages are computed
   in Python, not by the LLM.
7. **Answer + evidence** — templates render INR-formatted values; the
   evidence block records the date range, operation, filters, and matched
   record count.
8. **Remember** — the structured `ConversationContext` (last intent, metric,
   vendor, date range) is updated so follow-ups inherit context.

---

## Hallucination guardrails

- **No SQL from the LLM.** The LLM emits a closed-allowlist JSON object;
  there is no path from model output to raw SQL.
- **No numbers from the LLM.** All amounts/counts/percentages come from the
  engine; answers are rendered by templates.
- **Closed vocabulary.** Intents, metrics, filters, aggregations, group-bys
  and date-range types are enums; `extra="forbid"` kills invented fields.
- **Explicit refusal taxonomy** (`unsupported_metric`, `unsupported_field`,
  `ambiguous`, `invalid_structure`, `no_data`) — the assistant says what it
  can't do instead of inventing an answer, and offers suggestions.
- **Salaries / payroll / taxes / revenue / profit / forecasts** are matched
  as unsupported domains by both providers and refused.
- **Ambiguity detection** — "How much did we spend last month?" without a
  subject asks a clarifying question with clickable options.
- **Re-validation after the LLM.** The draft JSON must pass
  `FinancialQuery.model_validate` regardless of which provider produced it.
- **Deterministic date resolution.** The LLM's own date strings are never
  used for filtering.

---

## Evidence system

Every grounded answer carries an evidence object (rendered in the UI behind
"✓ Grounded — view how this was calculated"):

```json
{
  "how_calculated": {
    "date_range": "Aug 2026",
    "operation": "SUM(payout_amount)",
    "records_matched": 139,
    "filters": {}
  },
  "source": "PostgreSQL — artha financial dataset (deterministic query engine)",
  "grounded": true,
  "breakdown": [ ... ],
  "records": [ ... up to 20 rows ... ]
}
```

Comparisons include the second period's evidence too, so both sides of the
percentage are auditable.

---

## Database & seed data

Four tables (see `app/models/entities.py`), indexed for the question types
above (`ix_txn_date_status`, `ix_payout_vendor_date`, …) and designed toward
20M rows:

| Table | Purpose |
|---|---|
| `vendors` | 40 vendors with categories, accounts, payment terms |
| `transactions` | 8,000 bank transactions (debit/credit, reconciliation link) |
| `vendor_payouts` | 1,440 payouts to vendors (paid/pending/failed) |
| `reconciliation` | reconciliation records for transactions |

The synthetic generator (`app/services/seed_data.py`, `seed=42`) covers the
12 months ending with the last completed calendar month, with amounts,
categories, and payout statuses deterministic per seed — identical data on
every machine. Monthly distribution is uniform with a slight uptick toward
recent months so "last month" is never empty.

`CSV_COLUMNS` in the same file documents the column names the loader
expects, which is also the contract for the official dataset.

---

## API reference

### `GET /api/health`
```json
{"status":"ok","database":"connected","llm_provider":"rule_based",
 "record_counts":{"vendors":40,"transactions":8000,"vendor_payouts":1440,"reconciliation":8000}}
```

### `POST /api/chat`
```json
{"question": "How much did we spend on vendor payouts last month?",
 "conversation_id": "optional-id"}
```
Returns `answer`, `evidence`, `query` (the validated structured query),
`refusal` (or null), `meta` (provider, model, understanding latency,
grounded flag), `conversation_id`.

### `POST /api/query`
Executes a **structured query directly** (no LLM) — the programmatic path:
```json
{"intent":"top_vendors","metric":"payout_amount","aggregation":"sum",
 "group_by":["vendor"],
 "date_range":{"type":"calendar_month","start":"2026-08-01","end":"2026-08-31"},
 "limit":3}
```
Relative ranges (`calendar_month` without dates) are only valid through
`/api/chat`, where the backend resolves them.

---

## Tests & evaluation

```bash
cd backend && .venv/bin/python -m pytest -q      # 37 tests
python evaluation/run_eval.py --provider rule_based
python evaluation/run_eval.py --provider anthropic   # needs ANTHROPIC_API_KEY + seeded DB
```

- **37/37 tests pass** (query engine, guardrails, understanding, API grounding).
- Evaluation harness scores the 11-case benchmark (intents, date ranges,
  refusals) plus per-case latency; last rule-based run: **11/11 accuracy**.
- Tests run on SQLite in-memory (env vars set in `conftest.py` before app
  import); the engine is deliberately free of PostgreSQL-only SQL so both
  databases share one code path.

---

## Swapping in the official dataset

1. Export the official data as four CSVs named `vendors.csv`,
   `transactions.csv`, `vendor_payouts.csv`, `reconciliation.csv` with the
   columns documented in `CSV_COLUMNS` (`app/services/seed_data.py`).
2. Load without `--generate`:
   ```bash
   python scripts/load_data.py --data-dir /path/to/csvs --drop
   ```
   The loader tolerates missing optional fields and several date formats;
   rows missing required keys are skipped and reported.
3. No application code changes — the engine only knows the semantic layer.

---

## Scaling toward 20M records

The schema is indexed for every filter/group combination the semantic layer
allows; the engine emits a fixed family of parameterized SELECTs, so growth
means swapping the seed for the real dataset, not new SQL. For 20M rows the
next steps (intentionally out of scope here) would be partitioning
`transactions`/`vendor_payouts` by month, materialized vendor-month
summaries, and moving list pagination to keyset — the API contract would not
change.

---

## Assumptions

- **Currency is INR**; amounts are formatted as lakh/crore (the sample data
  and schema doc are Indian-finance oriented).
- The "current month" for relative dates is the server's local clock at
  request time; `resolve_date_range` always receives it explicitly so tests
  and evaluation pin `today`.
- Seed scale (40 vendors / 8k transactions / 1.4k payouts) is chosen to demo
  every question type with non-empty, plausible answers; it is not a
  performance benchmark — see the scaling section.
- Conversation memory is in-process (per API worker). For multi-worker
  deployments it would move to Postgres or Redis; the interface
  (`ConversationStore`) wouldn't change.
- The rule-based provider is a *fallback*, not the product: it handles the
  demo question families deterministically. With an API key set, the
  Anthropic provider handles arbitrary phrasing within the same allowlists.
- Vendor name matching is exact-on-canonical-name (the provider resolves
  fuzzy mentions against the vendor list from the DB before validating).

---

## Intentionally not implemented

Per the spec, these are explicitly out of scope for Version 0:

- **Text-to-SQL** — the LLM never writes SQL; it fills a schema.
- **RAG / vector databases / embeddings** — retrieval isn't needed when all
  answers come from parameterized queries.
- **Financial Twin** — future milestone.
- **Multi-agent orchestration** — a single structured-understanding call +
  deterministic pipeline covers all supported questions.
- **Authentication / multi-tenancy** — single-company demo.
- **Hardcoded demo answers** — everything is computed live from the database.
- **Chat history persistence** — conversation memory is in-process and
  structured; no transcript storage.
- **Write operations** — the assistant is read-only by design.
