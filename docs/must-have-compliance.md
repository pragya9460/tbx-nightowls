# Must-Have Compliance — TBX Problem Statement

Status as of the Financial-Twin pass. Every ✅ below is backed by named tests
(`backend/tests/`) and was verified through the live API. Regenerate test
results with `cd backend && .venv/bin/python -m pytest -q`.

| Requirement | Status | Implementation | Test Coverage | Demo Question |
|---|---|---|---|---|
| **1. Natural-language query handling** | ✅ | `RuleBasedProvider` + `AnthropicProvider` → `FinancialQuery` (closed allowlists); 10 intents; filters for bank/account/type/description/reference/UTR/thresholds; 13 date-range types resolved server-side (IST). "Last month" = previous **calendar** month (tested, not 30 days). | `test_query_understanding.py` (37), `test_query_engine.py` date-grammar tests (12+), `test_status_confidence.py` | "How much did I spend last month?" |
| **2. Grounded retrieval** | ✅ | LLM emits draft query JSON only; Pydantic validation gates execution; deterministic text-to-SQL (single parameterized SELECT, read-only DB session); answers rendered from `QueryResult` only (grounding contract in `app/query_engine/result.py`). | `test_grounding_contract.py` (8: no-DB-access proof, tamper proof, LLM-text exclusion, one-way masks), `test_guardrails.py` injection tests | "Show my largest transactions." |
| **3. Accurate computation** | ✅ | Engine computes SUM/COUNT/AVG/MAX/MIN, GROUP BY (bank/account/type/month), sorting, limits, date/type/description/amount filters in the database; matched-record count pre-limit; grouped totals recomputed over ALL matched rows when limited. Ground-truth tests compare engine output against independent Python/SQL-computed expected values. | `test_query_engine.py` (45+) — expected values computed independently via SQL in tests | "Which bank holds the most money?" |
| **4. Verifiable answers** | ✅ | Every response: `answer` + `evidence.how_calculated` (date range, operation, records_matched, filters, sql, cache_hit) + `breakdown`/`records` (≤15, masked) + comparison block for both periods. UI renders ANSWER / HOW I GOT THIS / SOURCE RECORDS zones. Evidence is built from the engine result — same records the calculation used (tested). | `test_api_grounding.py` (13), `test_grounding_contract.py::test_fabricated_evidence_record_does_not_propagate` | "Show me the records behind that." |
| **5. Hallucination guardrails** | ✅ | Explicit states in every response: `supported` / `empty_data` / `ambiguous` / `unsupported` / `invalid`. Unsupported domains (payroll, taxes, invoices, vendors, profit, forecasts, escrow, customers, loans) refused with the missing domain named. Ambiguity clarified ("How much moved?" → clarification; bare "spent" → debit, stated explicitly). Invalid structured queries rejected pre-execution. | `test_guardrails.py` (29), `test_status_confidence.py` (state tests) | "How much did we spend on employee salaries?" → refusal |
| **6. Lightweight model** | ✅ | `claude-haiku-4-5` default (smallest capable); provider abstraction (`build_provider`); rule-based provider runs the full benchmark with **0 tokens**; eval captures provider, model, latency, **token usage**, failure category. Foundation doc: `docs/model-evaluation.md` with thresholds and rationale. | eval harness (33 cases, `results.json`), `test_query_understanding.py` | (benchmark-driven; no single demo question) |
| **7. Multi-turn conversation** | ✅ | Structured `ConversationContext` (intent/metric/type/bank/range/filters) — no transcript dumping. Month-swap ("What about July?"), **filter refinement** ("Only those above ₹50,000." inherits listing + merges threshold), comparison ("How does that compare…"). Bank-count questions ("How many total number of banks are there") route to a dedicated `bank_count` intent (compiled SELECT over `bank`). | `test_query_understanding.py` context tests, `test_status_confidence.py` refinement tests (5, incl. end-to-end), `test_bank_count.py` | "What about July?" |
| **8. Explainability** | ✅ | Evidence exposes structured audit metadata: intent/metric (in `query`), date range, filters, records matched, operation, grouping, result (`summary`), breakdown, source records — via `query` + `evidence` fields. No chain-of-thought exposed (none exists — answers are templated). | `test_api_grounding.py` grounding tests, contract tests | Any question → expand "✓ Grounded" |

## Cross-cutting requirements

| Requirement | Status | Evidence |
|---|---|---|
| **Grounding contract** | ✅ | `app/query_engine/result.py` documents the `QueryResult` boundary (computation → generation); `tests/test_grounding_contract.py` proves: answer generator has no DB access, only engines construct results, result is the sole numeric source, masks are one-way. |
| **No hardcoded demo answers** | ✅ | No financial constants in `services/answers.py` (templates format engine values only — enforced by contract tests); every question, demo or arbitrary, flows through the same `ChatService.handle()` path (single entry point, no special-casing); `grep` for hardcoded totals returns nothing. |
| **Schema grounding** | ✅ | `supported_capabilities()` is the single source of truth; closed enums + `extra="forbid"` reject unknown intents/metrics/dimensions/filters/aggregations/date types **before** DB execution; no text-to-SQL from free text (the SQL compiler consumes validated queries only). |

## Bonus items

| Bonus | Status | Notes |
|---|---|---|
| **Confidence signalling** | ✅ | Interpretable, categorical — no fake probabilities: `high` (≥5 matched records, deterministic), `limited` (valid query, 1–4 matched records), `no_matches` (valid query, zero records — a real zero), `none` (refusal/invalid, nothing executed). Each response includes `confidence_basis` explaining the signal in words; the UI renders a colour-coded pill with the basis as tooltip. |
| **Anomaly callout** | ✅ | Deterministic rule in `app/services/anomaly.py`: transaction is anomalous iff `amount > multiplier × counterparty historical_average` (history excludes the transaction under test; ≥5 records required else never flagged). Multiplier/min-history env-configurable (`ARTHA_ANOMALY_MULTIPLIER`, `ARTHA_ANOMALY_MIN_HISTORY`). No ML, no LLM judgement. API `GET /api/twin/anomalies`; chat "Any unusual transactions?"; surfaced in the UI Alerts card. |
| **CSV / Excel export** | ✅ | `POST /api/export/evidence` returns CSV (default) or Excel (openpyxl); rows are passed verbatim from the evidence block, so exports contain exactly what was displayed (masked, capped — enforced by test, incl. CSV==Excel parity). |

## Semantic knowledge layer (ported from the `PythonCode/rag-api` prototype)

First-class RAG inside the grounded backend — the prototype's capability,
rebuilt without langchain and under Artha's grounding rules. Off by default
(`ARTHA_KNOWLEDGE_ENABLED=1` in docker-compose enables it).

| Capability | Status | Notes |
|---|---|---|
| **Unified `POST /api/ask`** | ✅ | rag-api request shape: `{question, top_k, threshold, filter, query_type}` with `auto`/`analytics`/`semantic`. `auto` = grounded engine first; the semantic path is consulted only when the engine **refuses** (unsupported/ambiguous/invalid). A real zero from the engine stays a real zero (tested). |
| **Knowledge store** | ✅ | ChromaDB (persistent volume) + local ONNX MiniLM embeddings — no API key, no network at query time. Seeded at startup from `knowledge/` mount; ingest text/upload/search/collections endpoints under `/api/knowledge/*`. |
| **PII masking at ingestion** | ✅ | `account_number` / `utr_number` masked before embedding — content, metadata, and CSV-row paths all pass the mask boundary (enforced by `test_knowledge.py`, one-way masks verified). |
| **Grounded RAG answers** | ✅ | LLM sees only retrieved passages; honest empty state when nothing matches; extractive fallback (no LLM) when no Anthropic key. |

## Financial Intelligence / Financial Twin (this pass)

First version of the twin layer — deterministic engines over the official
dataset plus clearly-labelled demo rules. Full detail:
`docs/financial-twin.md`, `architecture.md` §6.

| Capability | Status | Notes |
|---|---|---|
| **Financial Twin domain model** | ✅ | `FinancialTwinEngine` — accounts (balances read directly, never reconstructed from transactions), rules/reserves with `created/updated` timestamps and provenance, vendor profiles derived from real rows, reconciliation honest-absence adapter. Provenance levels: `OFFICIAL_DATASET` / `DERIVED` / `USER_PREFERENCE` / `SYNTHETIC_DEMO`, on every value. |
| **Protected reserves** | ✅ | Payroll ₹6,00,000 + GST ₹1,50,000, `protected: true`, labelled `SYNTHETIC_DEMO`; subtracted exactly once in the cash engine (no double counting — arithmetic identity tested). |
| **True available-cash engine** | ✅ | available_balance (official) − restricted (no source → 0, noted) − protected reserves (demo) − commitments (no source → 0, noted). Missing data never invented. `GET /api/twin/cash-position`; chat "How much cash do I really have?" / "Why is my cash lower…". |
| **"Can I pay X ₹Y?"** | ✅ | Deterministic feasibility: cash after, reserve violation, min-buffer violation, approval threshold, derived vendor history. Structured result + reasons + provenance. **Never executes a payment** (tested; no pay endpoint exists). |
| **What-if simulation** | ✅ | Static before → payment → after with per-rule outcomes (✓ preserved / ⚠ violated / approval-required), labelled assumptions. |
| **Vendor/counterparty intelligence** | ✅ | Deterministic description-format parsers (UPI/NEFT/IMPS/FT); aggregates computed from actual debit rows; one vendor's total verified against independent SQL in tests. Top vendors in UI + chat. |
| **Anomaly alerts in UI** | ✅ | Alerts card in the twin sidebar (`TwinPanel`) — amber callouts with ratio vs historical average. |
| **Reconciliation** | ⏸ Honest absence | Dataset has no reconciliation table; API returns `available: false` + the exact adapter interface a future table satisfies. Nothing fabricated. `dataset/extended_v1/` remains unloaded. |
| **Financial Twin UI** | ✅ | `TwinPanel` sidebar (cash position with components + provenance badges, top vendors, alerts, rules & reserves) — chat remains primary. |

## Good-to-Have demo questions (new this pass)

| # | Question | Result |
|---|---|---|
| 10 | "Can I pay Sharma Suppliers 400000 today?" | supported · financial_twin backend · affordability analysis with approval/reserve/buffer reasons |
| 11 | "What happens if I pay Sharma Suppliers 400000?" | what_if simulation: before → payment → after with rule outcomes |
| 12 | "How much cash do I really have?" | true available cash with per-component provenance |
| 13 | "Why is my available cash lower than my total balance?" | cash_position + explain — reserves called out |
| 14 | "Which vendors have the highest payouts?" | top vendors derived from transaction descriptions |
| 15 | "Any unusual transactions recently?" | anomaly scan over recent transactions |

## Demo flows verified (live API, Docker stack)

| # | Flow | Result |
|---|---|---|
| 1 | "How much did I spend last month?" | supported · high · SUM + records_matched |
| 2 | "Show me the records behind that." | transaction_list with evidence records |
| 3 | "What about July?" (follow-up) | same metric/type, new month |
| 4 | "How does that compare with August?" | comparison, both periods in evidence |
| 5 | "Which transactions are still unreconciled?" | refused: reconciliation data not in current dataset (correct — no such table) |
| 6 | "Show unreconciled transactions above ₹50,000." | refused (same reason) |
| 7 | "How much did we spend last month?" | debit interpretation, stated in answer |
| 8 | "How much did we spend on employee salaries?" | unsupported refusal |
| 9 | "What is our profit margin?" / "Which invoices are overdue?" | unsupported refusal (no derivation attempted) |

> Note on flows 5–6: the authoritative TBX schema has **no reconciliation
> table**. The earlier vendor-era flows are intentionally refused with the
> missing domain named — that IS the correct grounding behaviour. Equivalent
> supported flows: "Show transactions above ₹50,000." and "Only those above
> ₹50,000." (filter refinement).
