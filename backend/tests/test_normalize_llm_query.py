"""Tests for LLM query normalization."""
from __future__ import annotations

from app.llm.normalize import normalize_llm_query
from app.schemas.query import FinancialQuery, resolve_date_range, today


def test_normalize_ollama_vendor_expense_shape():
    raw = {
        "intent": "transaction_list",
        "filters": {
            "description_contains": "VENDOR",
            "transaction_type": "debit",
            "date_range": "calendar_month",
            "month": "july",
        },
        "metric": "amount",
        "aggregation": "sum",
    }
    cleaned = normalize_llm_query(raw)
    cleaned["date_range"] = resolve_date_range(
        today(), cleaned["date_range"]
    ).model_dump(mode="json", exclude_none=True)
    fq = FinancialQuery.model_validate(cleaned)
    assert fq.intent.value == "transaction_summary"
    assert fq.metric.value == "transaction_amount"
    assert fq.aggregation.value == "sum"
    assert fq.filters.transaction_type == "debit"
    assert fq.filters.description_contains is None
    assert fq.date_range.start.month == 7


def test_normalize_metric_aliases():
    cleaned = normalize_llm_query({
        "intent": "transaction_summary",
        "metric": "spend",
        "aggregation": "sum",
        "filters": {},
        "date_range": {"type": "calendar_month"},
    })
    assert cleaned["metric"] == "transaction_amount"


def test_normalize_month_list_to_named_comparison():
    cleaned = normalize_llm_query({
        "intent": "comparison",
        "metric": "amount",
        "aggregation": "sum",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "calendar_month", "month": ["july", "august"]},
        "comparison": {"against": "previous_period"},
    })
    assert cleaned["intent"] == "comparison"
    assert cleaned["date_range"]["month"] == "july"
    assert cleaned["comparison"] == {"against": "named_month", "month": "august"}


def test_normalize_comparison_against_month_name():
    cleaned = normalize_llm_query({
        "intent": "comparison",
        "metric": "transaction_amount",
        "aggregation": "sum",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "calendar_month", "month": "july"},
        "comparison": {"against": "august"},
    })
    assert cleaned["comparison"] == {"against": "named_month", "month": "august"}


def test_normalize_comparison_string_against():
    cleaned = normalize_llm_query({
        "intent": "comparison",
        "metric": "transaction_amount",
        "aggregation": "sum",
        "filters": {},
        "date_range": {"type": "calendar_month", "month": "july"},
        "comparison": "previous_period",
    })
    assert cleaned["comparison"] == {"against": "previous_period"}

