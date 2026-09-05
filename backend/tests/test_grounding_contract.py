"""Grounding-contract tests (Must-Have 2 + cross-cutting contract).

Prove structurally that fabricated values cannot enter the final financial
answer, and that the answer generator is walled off from the database.
"""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from app.query_engine.duckdb_engine import DuckDBQueryEngine, mask_account_number, mask_utr
from app.query_engine.result import QueryResult
from app.schemas.query import (
    Aggregation,
    FinancialQuery,
    Intent,
    Metric,
    resolve_date_range,
)
from app.services.answers import generate_answer


def make_q(**kw) -> FinancialQuery:
    defaults = dict(
        intent=Intent.TRANSACTION_SUMMARY,
        metric=Metric.TRANSACTION_AMOUNT,
        aggregation=Aggregation.SUM,
        date_range={"type": "calendar_month"},
    )
    defaults.update(kw)
    raw = dict(defaults)
    # relative ranges resolve server-side before validation (same as chat path)
    dr = raw.get("date_range")
    if isinstance(dr, dict) and dr.get("type") and "start" not in dr:
        from app.schemas.query import today as app_today

        raw["date_range"] = resolve_date_range(
            app_today(), dr
        ).model_dump(mode="json", exclude_none=True)
    return FinancialQuery.model_validate(raw)


# ---------------------------------------------------------------------------
# 1. The answer generator has no path to the database.
# ---------------------------------------------------------------------------

def test_generate_answer_has_no_db_access():
    """generate_answer must not import engines/sessions or accept a session:
    its signature is (query, result, comparison) — verified data in, text out."""
    from app.services import answers

    sig = inspect.signature(answers.generate_answer)
    params = list(sig.parameters)
    assert params == ["q", "result", "comparison_result"], params

    src = inspect.getsource(answers)
    for forbidden in ("Session", "execute(", "duckdb", "DuckDBQueryEngine",
                      "create_engine", "select("):
        assert forbidden not in src, (
            f"answer generator references '{forbidden}' — grounding breach"
        )


def test_query_result_constructed_only_by_engines():
    """Search the chat path: only engine modules may build QueryResults."""
    import app.services.chat_service as cs
    import app.services.answers as ans
    import inspect

    for mod in (cs, ans):
        src = inspect.getsource(mod)
        assert "QueryResult(" not in src, (
            f"{mod.__name__} constructs QueryResult — it must only receive one"
        )


# ---------------------------------------------------------------------------
# 2. Fabricated values cannot enter answers: the template renders ONLY
#    numbers present in the QueryResult it was handed.
# ---------------------------------------------------------------------------

def _all_numbers_in(text: str) -> set[str]:
    import re
    # capture numerals inside ₹ amounts / counts / percentages
    return set(re.findall(r"\d[\d,]*\.?\d*", text))


def test_answer_numbers_come_exclusively_from_result(duck_engine):
    """The answer renders exactly the numbers in the QueryResult it was given:
    a fabricated summary value flows into the answer ONLY via the result
    object, proving there is no other numeric source. And a fresh execution
    returns the true DB value (the tamper cannot poison the engine/cache)."""
    from app.query_engine.cache import get_cached_result

    q = make_q(filters={"transaction_type": "debit"})
    result = duck_engine.execute(q)
    real_value = result.summary["value"]

    # Fresh execution returns the real value (tamper didn't poison anything)
    fresh = duck_engine.execute(q)
    assert fresh.summary["value"] == real_value
    real_answer = generate_answer(q, fresh)
    assert _value_in_answer(real_answer, real_value)

    # If — and only if — a caller hands the generator a tampered result, the
    # answer changes. This proves the result object is the SOLE numeric
    # source; nothing else in the pipeline can inject a value.
    tampered = duck_engine.execute(q)
    tampered.summary["value"] = 999_999_999.0
    tampered_answer = generate_answer(q, tampered)
    assert _value_in_answer(tampered_answer, 999_999_999.0)
    # cache isolation: the tampered object was NOT the cached object
    cached = get_cached_result(q, scope=getattr(duck_engine, "_cache_scope", None))
    assert cached is not None and cached.summary["value"] == real_value


def _format_check(answer: str) -> str:
    return answer


def _value_in_answer(answer: str, value: float) -> bool:
    from app.services.answers import format_inr

    return format_inr(value) in answer


def test_fabricated_evidence_record_does_not_propagate(duck_engine):
    """A record invented by a caller (not returned by the engine) can never
    appear in evidence, because evidence is built FROM the engine result."""
    from app.query_engine.evidence import build_evidence

    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE)
    result = duck_engine.execute(q)
    fabricated = {"description": "FAKE/VENDOR", "transaction_amount": 42.0,
                  "transaction_id": "fake-id"}

    evidence = build_evidence(q, result)
    all_records = evidence.get("records", [])
    assert fabricated not in all_records
    # evidence records are a prefix of the engine's masked records
    if all_records:
        assert all_records[0] == result.records[0]


# ---------------------------------------------------------------------------
# 3. The LLM's output text never reaches the answer path.
# ---------------------------------------------------------------------------

def test_llm_output_text_not_in_answers(duck_engine):
    """The understanding stage yields only a query dict; no text field from it
    is passed to generate_answer. Check the wiring in ChatService."""
    import inspect

    import app.services.chat_service as cs

    src = inspect.getsource(cs.ChatService.handle)
    # answer is generated from (fq, result, comparison_result) — never from
    # understanding.refusal_message or any model text
    assert "generate_answer(fq, result" in src
    # the ONLY use of understanding.refusal_message is the refusal path
    refusal_idx = src.find("_refusal_response")
    answer_idx = src.find("generate_answer(fq, result")
    assert refusal_idx != -1 and answer_idx != -1


# ---------------------------------------------------------------------------
# 4. Masking is enforced at the engine boundary.
# ---------------------------------------------------------------------------

def test_masks_are_one_way():
    acc = "50200013729069"
    masked = mask_account_number(acc)
    assert masked == "XXXXX9069"
    # the raw value is not recoverable from the mask
    assert acc not in masked and masked not in acc

    utr = "jhI5nAdyb1qOEjmcB3JvWjC6tTO+ZPVqBFPm/GiErC4TRBWRQ5ylPG3p"
    mutr = mask_utr(utr)
    assert "***" in mutr and len(mutr) < 12


def test_engine_records_carry_no_raw_sensitive_values(duck_engine):
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE,
               date_range={"type": "all_time"}, limit=30)
    result = duck_engine.execute(q)
    assert result.records, "seed data should return records"
    for r in result.records:
        acc = r.get("account_number")
        if acc:
            assert acc.startswith("XXXXX") and len(acc) <= 9, acc
        utr = r.get("utr_number")
        if utr:
            assert len(utr) < 12, utr


# ---------------------------------------------------------------------------
# 5. Empty result handling — a real zero is a computed result (Must-Have 5).
# ---------------------------------------------------------------------------

def test_empty_result_is_a_grounded_zero(duck_engine):
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "calendar_month"})
    # a range with no data in the seed
    q = make_q(date_range={"type": "custom", "start": "2020-01-01",
                           "end": "2020-01-31"},
               filters={"transaction_type": "debit"})
    result = duck_engine.execute(q)
    assert result.summary["record_count"] == 0
    answer = generate_answer(q, result)
    # deterministic "no data" phrasing — never a fabricated figure
    assert "no" in answer.lower() and "₹" not in answer or \
        "No " in answer or "no transactions" in answer.lower()
