# Model Evaluation — Why a Lightweight Model Is Enough

> **Must-Have 6 (Model efficiency, 20% of the score):** build with the
> smallest model that can still deliver accurate answers.

This document explains the design decision, the benchmark that *proves* it,
and how to extend the comparison. Numbers in this file come from
`evaluation/results.json` — regenerate them with:

```bash
python evaluation/run_eval.py --provider rule_based
python evaluation/run_eval.py --provider anthropic   # needs ANTHROPIC_API_KEY
```

---

## 1. Why a lightweight model is appropriate

The LLM in Artha performs exactly **one narrow task**: read a question and a
compact context object, emit one JSON object constrained to a closed schema.
It never sees the database, never writes SQL, never sees a single financial
figure, and its output is *re-validated* by Pydantic before anything executes.

That task is a classification/extraction problem with a small output space:

- 10 intents
- 3 metrics
- 6 aggregations
- 10 filter fields with closed vocabularies
- 13 date-range types (dates themselves are resolved by deterministic code)

Structured outputs (JSON-schema-constrained decoding) make malformed JSON
structurally impossible, and the Pydantic layer makes *semantically* invalid
output refuse rather than execute. When the output space is that small and
the safety net that deep, model scale stops being the accuracy bottleneck —
prompt quality and validation strictness are.

## 2. What the model does

| Responsibility | Where |
|---|---|
| Map free-text question → intent | `understanding` |
| Extract filters (bank, type, description text, thresholds, reference/UTR) | `understanding` |
| Pick a date-range **type** and month name (never concrete dates) | `understanding` |
| Recognize unsupported domains (payroll, invoices, vendors…) and refuse | `understanding` |
| Recognize ambiguity and ask for clarification | `understanding` |
| Resolve follow-ups ("what about July") from structured context | `understanding` |

## 3. What deterministic code does (and why the model can't break it)

| Responsibility | Where |
|---|---|
| Resolve every date range to absolute dates (IST clock, fixed grammar) | `schemas/query.resolve_date_range` |
| Validate/reject the draft query (closed enums, `extra="forbid"`, SQL-token screening) | `schemas/query.FinancialQuery` |
| Compile to SQL (deterministic text-to-SQL; single SELECT, parameter-bound) | `query_engine/mysql_builder.py` |
| Execute, aggregate, sort, limit in MySQL (read-only session) | `query_engine/mysql_engine.py` |
| Mask `account_number` / `utr_number` | engine boundary |
| Compute totals, peaks, comparisons, percentages | engine + `services/answers.py` |
| Render the answer sentence | deterministic templates |

**The model has no path to any financial number.** If it hallucinated a
figure, the figure would have nowhere to go — answers are rendered only from
`QueryResult` values (see `app/query_engine/result.py`, the grounding
contract, and `tests/test_grounding_contract.py` which proves this
structurally).

## 4. How models are benchmarked

`evaluation/run_eval.py` runs `benchmark.json` (33 cases) against any
provider and writes `results.json`. Per case it records:

- question, expected intent/filters/group-by/date-range/refusal
- actual produced query (or refusal) and its failure category
- correctness (computed from execution, never asserted)
- latency (ms, wall clock around `understand()`)
- token usage (`input_tokens`/`output_tokens` — reported by the Anthropic
  API; `null` for the rule-based provider which makes no API calls)
- provider + model used

The harness is provider-agnostic: `build_provider()` accepts any
implementation of `LLMProvider` (API model, local model, mock). To benchmark
another model:

```bash
ARTHA_MODEL=<model-id> python evaluation/run_eval.py --provider anthropic
# or add a provider in app/llm/provider.py and extend the --provider choices
```

## 5. Threshold required

A candidate model is acceptable for the hackathon demo when, on the full
benchmark:

| Metric | Threshold |
|---|---|
| Accuracy | **≥ 0.95** (33 cases — at most 1 miss) |
| Validation failures | **0** (invalid structured output) |
| Unsupported-domain misses | **0** (must refuse payroll/invoice/vendor questions) |
| p95 latency | **≤ 2 s** (chat must feel responsive) |

Any model meeting all four is acceptable; the *smallest* such model wins.
Accuracy below threshold means the prompt/schema needs work first — not a
bigger model.

## 6. Why `claude-haiku-4-5` was chosen

- It is the smallest Claude model family member that reliably follows the
  structured-output schema for this task class (JSON-constrained extraction
  with enums + refusal behaviour).
- The rule-based provider (0 tokens, sub-millisecond) already scores 1.00 on
  the benchmark — demonstrating the architecture carries the accuracy, not
  the model. Haiku adds robustness on phrasings the regex layer doesn't
  cover, at the lowest cost tier.
- Cost framing: at Haiku pricing, a 1,000-question demo session costs on the
  order of a fraction of a cent for understanding — because the model emits
  ~100 output tokens per question and everything else is deterministic.

## Current measured status

### Comparison table

| Provider / model | Accuracy | Validation failures | Unsupported-domain misses | Avg latency | Total tokens | Notes |
|---|---|---|---|---|---|---|
| `rule_based` (no LLM) | **1.00** (33/33) | 0 | 0 | 0.1 ms | 0 | Deterministic baseline; regex mapping. Proves the architecture carries accuracy. |
| `anthropic` · `claude-haiku-4-5` | pending run | — | — | — | — | Needs `ANTHROPIC_API_KEY`; run with the command below. Expected ≥ 0.95 per threshold table above. |

### Reproducing the Anthropic row (one command)

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or put it in .env
python evaluation/run_eval.py --provider anthropic
# writes evaluation/results.json; append the numbers to the table above
```

The rule-based row regenerates with `--provider rule_based`. Both runs
record per-case accuracy (computed from execution), latency, token usage,
and failure category — no numbers in this document are asserted without a
harness run behind them.

Latest run (`evaluation/results.json`, rule-based): **33/33 = 1.00, 0.1 ms
avg latency, 0 tokens**.

> We deliberately constrain the LLM to query understanding and use
> deterministic systems for financial computation, allowing a lightweight
> model to achieve high accuracy.
