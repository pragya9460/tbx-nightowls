"""Guardrail tests: schema safety, unsupported questions, ambiguity,
invalid structures. The semantic layer must reject everything outside its
allowlist."""
from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.llm.provider import RuleBasedProvider
from app.schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    Intent,
    Metric,
    QueryFilters,
    QueryRefusalReason,
    refusal,
    supported_capabilities,
)


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "vendor_payout_summary",
            "metric": "payout_amount",
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
            "made_up_field": 42,  # not in schema — must be rejected
        })


def test_unknown_intent_rejected():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "employee_salary_summary",  # not in enum
            "metric": "payout_amount",
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
        })


def test_unknown_metric_rejected():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "vendor_payout_summary",
            "metric": "net_profit",  # not in enum
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
        })


def test_invalid_date_range_rejected():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "vendor_payout_summary",
            "metric": "payout_amount",
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-31", "end": "2026-08-01"},  # start > end
        })


def test_invalid_payout_status_filter_rejected():
    with pytest.raises(ValidationError):
        QueryFilters(payout_status="maybe")


def test_intent_metric_mismatch_rejected():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "vendor_payout_summary",
            "metric": "transaction_amount",  # payout intent with txn metric
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
        })


def test_top_vendors_requires_group_by():
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate({
            "intent": "top_vendors",
            "metric": "payout_amount",
            "aggregation": "sum",
            "filters": {},
            "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
        })


def test_refusal_taxonomy():
    for reason in QueryRefusalReason:
        r = refusal(reason, "test message")
        assert r.reason == reason


def test_supported_capabilities_complete():
    caps = supported_capabilities()
    assert "vendor_payout_summary" in caps["intents"]
    assert "payout_amount" in caps["metrics"]
    assert "vendor_name" in caps["filters"]


# ----- rule-based understanding guardrails ----------------------------------

def test_unsupported_salary_question():
    p = RuleBasedProvider()
    u = p.understand("How much did we spend on employee salaries?")
    assert u.refusal_reason == "unsupported"
    assert "payroll" in u.refusal_message.lower() or "not available" in u.refusal_message.lower()


def test_unsupported_tax_question():
    p = RuleBasedProvider()
    u = p.understand("How much GST did we pay last quarter?")
    assert u.refusal_reason == "unsupported"


def test_unsupported_revenue_question():
    p = RuleBasedProvider()
    u = p.understand("What was our revenue last month?")
    assert u.refusal_reason == "unsupported"


def test_ambiguous_spend_question():
    p = RuleBasedProvider()
    u = p.understand("How much did we spend last month?")
    assert u.refusal_reason == "ambiguous"


def test_valid_question_maps_to_query():
    p = RuleBasedProvider()
    u = p.understand("How much did we spend on vendor payouts last month?")
    assert u.query is not None
    q = FinancialQuery.model_validate(u.query)
    assert q.intent == Intent.VENDOR_PAYOUT_SUMMARY
    assert q.metric == Metric.PAYOUT_AMOUNT
