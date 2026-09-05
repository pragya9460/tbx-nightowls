# Must-Have Compliance — TBX Problem Statement

Status as of the hardening pass. Every ✅ below is backed by named tests
(`backend/tests/`) and was verified through the live API. Regenerate test
results with `cd backend && .venv/bin/python -m pytest -q`.

| Requirement | Status | Implementation | Test Coverage | Demo Question |
|---|---|---|---|---|
| **1. Natural-language query handling** | ✅ | `RuleBasedProvider` + `AnthropicProvider` → `FinancialQuery` (closed allowlists); 10 intents; filters for bank/account/type/description/reference/UTR/thresholds; 13 date-range types resolved server-side (IST). "Last month" = previous **calendar** month (tested, not 30 days). | `test_query_understanding.py` (37), `test_query_engine.py` date-grammar tests (12+), `test_status_confidence.py` | "How much did I spend last month?" |
| **2. Grounded retrieval** | ✅ | LLM emits draft query JSON only; Pydantic validation gates execution; deterministic text-to-SQL (sqlglot-verified single SELECT, parameter-bound, read-only DuckDB); answers rendered from `QueryResult` only (grounding contract in `app/query_engine/result.py`). | `test_grounding_contract.py` (8: no-DB-access proof, tamper proof, LLM-text exclusion, one-way masks), `test_guardrails.py` injection tests | "Show my largest transactions." |
| **3. Accurate computation** | ✅ | Engine computes SUM/COUNT/AVG/MAX/MIN, GROUP BY (bank/account/type/month), sorting, limits, date/type/description/amount filters in DuckDB; matched-record count pre-limit; grouped totals recomputed over ALL matched rows when limited. Ground-truth tests compare engine output against independent Python/SQL-computed expected values. | `test_query_engine.py` (45+) — expected values computed independently via SQL in tests | "Which bank holds the most money?" |
| **4. Verifiable answers** | ✅ | Every response: `answer` + `evidence.how_calculated` (date range, operation, records_matched, filters, sql, cache_hit) + `breakdown`/`records` (≤15, masked) + comparison block for both periods. UI renders ANSWER / HOW I GOT THIS / SOURCE RECORDS zones. Evidence is built from the engine result — same records the calculation used (tested). | `test_api_grounding.py` (13), `test_grounding_contract.py::test_fabricated_evidence_record_does_not_propagate` | "Show me the records behind that." |
| **5. Hallucination guardrails** | ✅ | Explicit states in every response: `supported` / `empty_data` / `ambiguous` / `unsupported` / `invalid`. Unsupported domains (payroll, taxes, invoices, vendors, profit, forecasts, escrow, customers, loans) refused with the missing domain named. Ambiguity clarified ("How much moved?" → clarification; bare "spent" → debit, stated explicitly). Invalid structured queries rejected pre-execution. | `test_guardrails.py` (29), `test_status_confidence.py` (state tests) | "How much did we spend on employee salaries?" → refusal |
| **6. Lightweight model** | ✅ | `claude-haiku-4-5` default (smallest capable); provider abstraction (`build_provider`); rule-based provider runs the full benchmark with **0 tokens**; eval captures provider, model, latency, **token usage**, failure category. Foundation doc: `docs/model-evaluation.md` with thresholds and rationale. | eval harness (33 cases, `results.json`), `test_query_understanding.py` | (benchmark-driven; no single demo question) |
| **7. Multi-turn conversation** | ✅ | Structured `ConversationContext` (intent/metric/type/bank/range/filters) — no transcript dumping. Month-swap ("What about July?"), **filter refinement** ("Only those above ₹50,000." inherits listing + merges threshold), comparison ("How does that compare…"). | `test_query_understanding.py` context tests, `test_status_confidence.py` refinement tests (5, incl. end-to-end) | "What about July?" |
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
| **Confidence signalling** | ✅ | Interpretable signals, no fake probabilities: `confidence: high` (supported, deterministic), `medium` (valid query, zero records — a real zero), `none` (refusal, nothing executed). Each response includes `confidence_basis` explaining the signal in words. |
| **Anomaly callout** | ⏸ Deferred | Deliberately deferred to the next iteration: the current dataset lacks the per-vendor history dimension that would make callouts meaningful; `dataset/extended_v1/` (not yet loaded) is the intended data source. Documented in `architecture.md` §13. |
| **CSV / Excel export** | ✅ | `POST /api/export/evidence` returns CSV (default) or Excel; rows are passed verbatim from the evidence block, so exports contain exactly what was displayed (masked, capped — enforced by test). |

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
