"""Query-understanding tests (rule-based provider — the deterministic
baseline; the same cases form the evaluation benchmark)."""
from __future__ import annotations

import pytest

from app.llm.provider import RuleBasedProvider
from app.schemas.query import (
    Aggregation,
    FinancialQuery,
    Intent,
    Metric,
)


@pytest.fixture()
def provider(vendor_names):
    return RuleBasedProvider(vendor_names=vendor_names)


def _validate(u):
    assert u.query is not None, f"expected a query, got refusal: {u.refusal_message}"
    return FinancialQuery.model_validate(u.query)


def test_payout_summary_last_month(provider):
    q = _validate(provider.understand("How much did we pay vendors last month?"))
    assert q.intent == Intent.VENDOR_PAYOUT_SUMMARY
    assert q.metric == Metric.PAYOUT_AMOUNT
    assert q.aggregation == Aggregation.SUM
    assert q.date_range.type.value == "calendar_month"


def test_unreconciled_list(provider):
    q = _validate(provider.understand("Which transactions are still unreconciled?"))
    assert q.intent == Intent.UNRECONCILED_LIST
    assert q.aggregation == Aggregation.NONE


def test_vendor_specific(provider):
    q = _validate(provider.understand("How much did we pay ABC Suppliers last month?"))
    assert q.intent == Intent.VENDOR_SPEND
    assert q.filters.vendor_name == "ABC Suppliers"


def test_top_vendors(provider):
    q = _validate(provider.understand("Which vendors received the most money last month?"))
    assert q.intent == Intent.TOP_VENDORS
    assert "vendor" in [g.value for g in q.group_by]


def test_unreconciled_count(provider):
    q = _validate(provider.understand("How many transactions were unreconciled last month?"))
    assert q.intent == Intent.TRANSACTION_COUNT
    assert q.filters.reconciliation_status == "unreconciled"


def test_multi_month_range(provider):
    q = _validate(provider.understand("How much did we pay vendors in the last 3 months?"))
    assert q.date_range.type.value == "last_n_months"
    assert q.date_range.n_months == 3


def test_comparison_follow_up(provider):
    ctx = {
        "last_intent": "vendor_payout_summary",
        "last_metric": "payout_amount",
        "last_date_range": {"type": "calendar_month", "start": "2026-08-01",
                            "end": "2026-08-31", "label": "Aug 2026"},
        "last_filters": {},
    }
    q = _validate(provider.understand("How does that compare with the month before?", context=ctx))
    assert q.intent == Intent.COMPARISON
    assert q.metric == Metric.PAYOUT_AMOUNT
