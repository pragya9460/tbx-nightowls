"""Query-engine tests: aggregation, grouping, listing, balance intents.

Expected values are computed independently via the ORM — the tests verify
the engine against ground truth, not against itself.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.models import Account, Bank, Transaction
from app.query_engine.engine import FinancialQueryEngine
from app.schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    GroupByDimension,
    Intent,
    Metric,
    SortDirection,
    resolve_date_range,
)


def make_q(**kw) -> FinancialQuery:
    defaults = dict(
        intent=Intent.TRANSACTION_SUMMARY,
        metric=Metric.TRANSACTION_AMOUNT,
        aggregation=Aggregation.SUM,
        date_range={"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    )
    filters = kw.pop("filters", {})
    defaults["filters"] = filters
    defaults.update(kw)
    return FinancialQuery.model_validate(defaults)


# ---------------------------------------------------------------------------
# Aggregation correctness against ORM ground truth
# ---------------------------------------------------------------------------

def test_debit_sum_aug_2026(db):
    engine = FinancialQueryEngine(db)
    result = engine.execute(make_q(filters={"transaction_type": "debit"}))
    expected = db.scalar(
        select(func.coalesce(func.sum(Transaction.transaction_amount), 0)).where(
            Transaction.transaction_type == "debit",
            func.date(Transaction.transaction_date) >= dt.date(2026, 8, 1),
            func.date(Transaction.transaction_date) <= dt.date(2026, 8, 31),
        )
    )
    assert result.summary["value"] == pytest.approx(float(expected), rel=1e-6)
    expected_n = db.scalar(
        select(func.count(Transaction.transaction_id)).where(
            Transaction.transaction_type == "debit",
            func.date(Transaction.transaction_date) >= dt.date(2026, 8, 1),
            func.date(Transaction.transaction_date) <= dt.date(2026, 8, 31),
        )
    )
    assert result.summary["record_count"] == expected_n


def test_credit_count_all_time(db):
    engine = FinancialQueryEngine(db)
    result = engine.execute(FinancialQuery.model_validate({
        "intent": "transaction_summary",
        "metric": "transaction_count",
        "aggregation": "count",
        "filters": {"transaction_type": "credit"},
        "date_range": {"type": "all_time"},
    }))
    expected = db.scalar(
        select(func.count(Transaction.transaction_id)).where(
            Transaction.transaction_type == "credit")
    )
    assert result.summary["value"] == expected


def test_avg_max_min(db):
    engine = FinancialQueryEngine(db)
    for agg in (Aggregation.AVG, Aggregation.MAX, Aggregation.MIN):
        r = engine.execute(make_q(aggregation=agg,
                                  filters={"transaction_type": "debit"}))
        fn = {Aggregation.AVG: func.avg, Aggregation.MAX: func.max,
              Aggregation.MIN: func.min}[agg]
        expected = db.scalar(
            select(fn(Transaction.transaction_amount)).where(
                Transaction.transaction_type == "debit",
                Transaction.transaction_date >= dt.datetime(2026, 8, 1),
                Transaction.transaction_date < dt.datetime(2026, 9, 1),
            )
        )
        assert r.summary["value"] == pytest.approx(float(expected), rel=1e-6)


def test_group_by_bank_matches_orm(db):
    engine = FinancialQueryEngine(db)
    q = make_q(group_by=[GroupByDimension.BANK])
    result = engine.execute(q)
    expected = dict(db.execute(
        select(Bank.bank_code, func.sum(Transaction.transaction_amount))
        .join(Account, Transaction.account_id == Account.account_id)
        .join(Bank, Account.bank_code == Bank.bank_code)
        .where(func.date(Transaction.transaction_date) >= dt.date(2026, 8, 1),
               func.date(Transaction.transaction_date) <= dt.date(2026, 8, 31))
        .group_by(Bank.bank_code)
    ).all())
    got = {b["bank_code"]: b["value"] for b in result.breakdown}
    for code, total in expected.items():
        assert got[code] == pytest.approx(float(total), rel=1e-6)


def test_group_by_transaction_type(db):
    engine = FinancialQueryEngine(db)
    q = make_q(group_by=[GroupByDimension.TRANSACTION_TYPE],
               date_range={"type": "all_time"})
    result = engine.execute(q)
    types = {b["transaction_type"]: b["value"] for b in result.breakdown}
    assert set(types) == {"debit", "credit"}
    expected_debit = db.scalar(select(func.sum(Transaction.transaction_amount))
                               .where(Transaction.transaction_type == "debit"))
    assert types["debit"] == pytest.approx(float(expected_debit), rel=1e-6)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_largest_transactions_sorted_desc(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE,
               filters={"transaction_type": "debit", "min_amount": 10000},
               limit=10)
    result = engine.execute(q)
    amounts = [r["transaction_amount"] for r in result.records]
    assert amounts == sorted(amounts, reverse=True)
    assert all(a >= 10000 for a in amounts)
    assert len(result.records) <= 10


def test_list_records_count_exceeds_shown(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE, limit=5)
    result = engine.execute(q)
    assert len(result.records) <= 5
    # matched count is the TRUE count pre-limit
    expected_all = db.scalar(select(func.count(Transaction.transaction_id)).where(
        func.date(Transaction.transaction_date) >= dt.date(2026, 8, 1),
        func.date(Transaction.transaction_date) <= dt.date(2026, 8, 31)))
    assert result.summary["record_count"] == expected_all


def test_description_search(db):
    engine = FinancialQueryEngine(db)
    # find a description substring that actually exists in the seed
    sample = db.scalar(select(Transaction.description).limit(1))
    needle = sample.split()[0]
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE,
               filters={"description_contains": needle}, limit=20)
    result = engine.execute(q)
    assert len(result.records) >= 1
    for r in result.records:
        assert needle.lower() in (r["description"] or "").lower()


# ---------------------------------------------------------------------------
# Balance / account / bank intents
# ---------------------------------------------------------------------------

def test_total_balance_matches_orm(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.ACCOUNT_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM,
               date_range={"type": "all_time"})
    result = engine.execute(q)
    expected = db.scalar(select(func.sum(Account.available_balance)))
    assert result.summary["value"] == pytest.approx(float(expected), rel=1e-6)
    assert result.summary["record_count"] == db.scalar(select(func.count(Account.account_id)))


def test_bank_highest_balance(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.BANK_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM, group_by=[GroupByDimension.BANK],
               date_range={"type": "all_time"}, limit=10)
    result = engine.execute(q)
    values = [b["value"] for b in result.breakdown]
    assert values == sorted(values, reverse=True)
    expected = dict(db.execute(
        select(Bank.bank_code, func.sum(Account.available_balance))
        .join(Account, Account.bank_code == Bank.bank_code)
        .group_by(Bank.bank_code)).all())
    for b in result.breakdown:
        assert b["value"] == pytest.approx(float(expected[b["bank_code"]]), rel=1e-6)


def test_single_account_balance(db):
    engine = FinancialQueryEngine(db)
    acct = db.scalar(select(Account).limit(1))
    q = make_q(intent=Intent.ACCOUNT_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM,
               filters={"account_id": acct.account_id},
               date_range={"type": "all_time"})
    result = engine.execute(q)
    assert result.summary["value"] == pytest.approx(float(acct.available_balance), rel=1e-6)


def test_account_count_by_bank(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.BANK_ACCOUNT_COUNT, metric=Metric.TRANSACTION_COUNT,
               aggregation=Aggregation.COUNT, date_range={"type": "all_time"})
    result = engine.execute(q)
    expected = dict(db.execute(
        select(Bank.bank_code, func.count(Account.account_id))
        .join(Account, Account.bank_code == Bank.bank_code)
        .group_by(Bank.bank_code)).all())
    got = {b["bank_code"]: b["value"] for b in result.breakdown}
    assert got == {k: v for k, v in expected.items()}


# ---------------------------------------------------------------------------
# Monthly trend + reference lookup
# ---------------------------------------------------------------------------

def test_monthly_trend_peak(db):
    engine = FinancialQueryEngine(db)
    q = make_q(intent=Intent.MONTHLY_TREND, group_by=[GroupByDimension.MONTH],
               date_range={"type": "all_time"})
    result = engine.execute(q)
    assert result.summary["peak_month"] is not None
    # independent peak computation
    rows = db.execute(
        select(func.strftime("%Y-%m", Transaction.transaction_date),
               func.sum(Transaction.transaction_amount))
        .group_by(func.strftime("%Y-%m", Transaction.transaction_date))
    ).all()
    expected_peak = max(rows, key=lambda r: r[1])[0]
    assert result.summary["peak_month"] == expected_peak
    months = [b["month"] for b in result.breakdown]
    assert months == sorted(months)


def test_reference_lookup_exact(db):
    engine = FinancialQueryEngine(db)
    txn = db.scalar(
        select(Transaction).where(Transaction.transaction_reference_id.isnot(None)).limit(1)
    )
    q = make_q(intent=Intent.REFERENCE_LOOKUP, metric=Metric.TRANSACTION_COUNT,
               aggregation=Aggregation.NONE,
               filters={"reference_id": txn.transaction_reference_id},
               date_range={"type": "all_time"}, limit=10)
    result = engine.execute(q)
    assert result.summary["record_count"] >= 1
    assert all(r["transaction_reference_id"] == txn.transaction_reference_id
               for r in result.records)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def test_comparison_jul_aug_2026(db):
    engine = FinancialQueryEngine(db)
    q = make_q(
        intent=Intent.COMPARISON,
        filters={"transaction_type": "debit"},
        comparison={"against": "previous_period"},
    )
    result = engine.execute(q)
    cmp_result = engine.execute_comparison(q, result, dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    expected_jul = db.scalar(
        select(func.coalesce(func.sum(Transaction.transaction_amount), 0)).where(
            Transaction.transaction_type == "debit",
            func.date(Transaction.transaction_date) >= dt.date(2026, 7, 1),
            func.date(Transaction.transaction_date) <= dt.date(2026, 7, 31),
        )
    )
    assert cmp_result.summary["value"] == pytest.approx(float(expected_jul), rel=1e-6)
    assert cmp_result.query_metadata["is_comparison_of"] is True
    assert cmp_result.query_metadata["date_range"]["label"] == "Jul 2026"


def test_comparison_month_before_previous_resolution():
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "month_before_previous"})
    assert (dr.start, dr.end) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


# ---------------------------------------------------------------------------
# Date-range resolution grammar (spec §8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ({"type": "this_month"}, (dt.date(2026, 9, 1), dt.date(2026, 9, 5))),
    ({"type": "yesterday"}, (dt.date(2026, 9, 4), dt.date(2026, 9, 4))),
    ({"type": "today"}, (dt.date(2026, 9, 5), dt.date(2026, 9, 5))),
    ({"type": "this_week"}, (dt.date(2026, 8, 31), dt.date(2026, 9, 5))),
    ({"type": "last_week"}, (dt.date(2026, 8, 24), dt.date(2026, 8, 30))),
    ({"type": "last_n_days", "n_days": 7}, (dt.date(2026, 8, 30), dt.date(2026, 9, 5))),
    ({"type": "last_n_days", "n_days": 30}, (dt.date(2026, 8, 7), dt.date(2026, 9, 5))),
    ({"type": "this_year"}, (dt.date(2026, 1, 1), dt.date(2026, 9, 5))),
    ({"type": "last_year"}, (dt.date(2025, 1, 1), dt.date(2025, 12, 31))),
    ({"type": "last_n_months", "n_months": 3}, (dt.date(2026, 6, 1), dt.date(2026, 8, 31))),
    ({"type": "calendar_month", "month": "june", "year": 2026},
     (dt.date(2026, 6, 1), dt.date(2026, 6, 30))),
    ({"type": "calendar_month"}, (dt.date(2026, 8, 1), dt.date(2026, 8, 31))),
])
def test_date_resolution_grammar(spec, expected):
    dr = resolve_date_range(dt.date(2026, 9, 5), spec)
    assert (dr.start, dr.end) == expected


def test_named_month_without_year_defaults_correctly():
    # "june" said in Sep 2026 → June 2026 (already passed this year)
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "calendar_month", "month": "june"})
    assert dr.start == dt.date(2026, 6, 1)
    # "december" said in Sep 2026 → December 2025 (hasn't happened yet)
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "calendar_month", "month": "december"})
    assert dr.start == dt.date(2025, 12, 1)


def test_date_range_start_after_end_rejected():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_summary", "metric": "transaction_amount",
            "aggregation": "sum", "date_range": {
                "type": "custom", "start": "2026-08-31", "end": "2026-08-01"},
        })
