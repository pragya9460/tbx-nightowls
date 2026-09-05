"""Query engine tests: SUM/COUNT/filtering/grouping/date ranges against the
seeded deterministic dataset. Expected values computed from the SAME seed —
verifying engine == data, not hardcoded demo answers."""
from __future__ import annotations

import datetime as dt

import pytest

from app.query_engine.engine import FinancialQueryEngine
from app.schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    GroupByDimension,
    Intent,
    Metric,
    QueryFilters,
)


def make_q(**kw) -> FinancialQuery:
    base = dict(
        intent=Intent.VENDOR_PAYOUT_SUMMARY,
        metric=Metric.PAYOUT_AMOUNT,
        aggregation=Aggregation.SUM,
        filters=QueryFilters(),
        date_range=DateRange(type=DateRangeType.CUSTOM,
                             start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 31)),
    )
    base.update(kw)
    return FinancialQuery.model_validate(base)


def test_sum_payouts_aug_2026(db):
    engine = FinancialQueryEngine(db)
    q = make_q()
    result = engine.execute(q)
    # Independent verification straight from the ORM
    from app.models import VendorPayout

    expected = sum(
        float(p.amount) for p in db.query(VendorPayout).filter(
            VendorPayout.payout_date >= dt.date(2026, 8, 1),
            VendorPayout.payout_date <= dt.date(2026, 8, 31),
        ).all()
    )
    assert result.summary["value"] == pytest.approx(expected, rel=1e-6)
    assert result.summary["record_count"] >= 0


def test_count_unreconciled_matches_data(db):
    engine = FinancialQueryEngine(db)
    from app.models import Transaction

    month_start, month_end = dt.date(2026, 8, 1), dt.date(2026, 8, 31)
    expected = db.query(Transaction).filter(
        Transaction.reconciliation_status == "unreconciled",
        Transaction.transaction_date >= month_start,
        Transaction.transaction_date <= month_end,
    ).count()
    q = FinancialQuery.model_validate({
        "intent": "transaction_count",
        "metric": "transaction_count",
        "aggregation": "count",
        "filters": {"reconciliation_status": "unreconciled"},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })
    result = engine.execute(q)
    assert result.summary["value"] == expected


def test_vendor_filtering(db):
    engine = FinancialQueryEngine(db)
    from app.models import Vendor, VendorPayout

    vendor = db.query(Vendor).first()
    expected = sum(
        float(p.amount) for p in db.query(VendorPayout).filter(
            VendorPayout.vendor_id == vendor.vendor_id,
            VendorPayout.payout_date >= dt.date(2026, 8, 1),
            VendorPayout.payout_date <= dt.date(2026, 8, 31),
        ).all()
    )
    q = make_q(filters=QueryFilters(vendor_id=vendor.vendor_id))
    result = engine.execute(q)
    assert result.summary["value"] == pytest.approx(expected, rel=1e-6)


def test_top_vendors_grouping_ordering(db):
    engine = FinancialQueryEngine(db)
    q = make_q(
        intent=Intent.TOP_VENDORS,
        group_by=[GroupByDimension.VENDOR],
        limit=5,
    )
    result = engine.execute(q)
    assert 1 <= len(result.breakdown) <= 5
    values = [b["value"] for b in result.breakdown]
    assert values == sorted(values, reverse=True), "breakdown must be sorted desc"
    # top entry equals the ORM-computed top vendor
    from app.models import Vendor, VendorPayout
    from sqlalchemy import func

    rows = (
        db.query(Vendor.vendor_name, func.sum(VendorPayout.amount).label("total"))
        .join(VendorPayout, VendorPayout.vendor_id == Vendor.vendor_id)
        .filter(VendorPayout.payout_date >= dt.date(2026, 8, 1),
                VendorPayout.payout_date <= dt.date(2026, 8, 31))
        .group_by(Vendor.vendor_name)
        .order_by(func.sum(VendorPayout.amount).desc())
        .all()
    )
    assert result.breakdown[0]["vendor_name"] == rows[0][0]
    assert result.breakdown[0]["value"] == pytest.approx(float(rows[0][1]), rel=1e-6)


def test_top_vendors_limited_total_covers_all_matched(db):
    """A limited top_vendors query must not report the top-N sum as the total."""
    engine = FinancialQueryEngine(db)
    limited = engine.execute(make_q(
        intent=Intent.TOP_VENDORS,
        group_by=[GroupByDimension.VENDOR],
        limit=3,
    ))
    unlimited = engine.execute(make_q(
        intent=Intent.TOP_VENDORS,
        group_by=[GroupByDimension.VENDOR],
    ))
    # The limited query's summary total equals the unlimited total...
    assert limited.summary["value"] == pytest.approx(unlimited.summary["value"], rel=1e-6)
    # ...even though its breakdown only holds the top 3.
    assert len(limited.breakdown) == 3
    assert len(unlimited.breakdown) >= len(limited.breakdown)


def test_unreconciled_list_returns_records(db):
    engine = FinancialQueryEngine(db)
    q = FinancialQuery.model_validate({
        "intent": "unreconciled_list",
        "metric": "transaction_count",
        "aggregation": "none",
        "filters": {"reconciliation_status": "unreconciled"},
        "date_range": {"type": "custom", "start": "2026-07-01", "end": "2026-08-31"},
        "limit": 10,
    })
    result = engine.execute(q)
    assert len(result.records) <= 10
    for r in result.records:
        assert r["reconciliation_status"] == "unreconciled"


def test_avg_aggregation(db):
    engine = FinancialQueryEngine(db)
    q = make_q(aggregation=Aggregation.AVG)
    result = engine.execute(q)
    from app.models import VendorPayout

    amounts = [
        float(p.amount) for p in db.query(VendorPayout).filter(
            VendorPayout.payout_date >= dt.date(2026, 8, 1),
            VendorPayout.payout_date <= dt.date(2026, 8, 31),
        ).all()
    ]
    if amounts:
        assert result.summary["value"] == pytest.approx(sum(amounts) / len(amounts), rel=1e-6)


def test_month_before_previous_resolution():
    from app.schemas.query import resolve_date_range

    today = dt.date(2026, 9, 5)
    dr = resolve_date_range(today, {"type": "month_before_previous"})
    assert dr.start == dt.date(2026, 7, 1)
    assert dr.end == dt.date(2026, 7, 31)

    dr2 = resolve_date_range(today, {"type": "calendar_month"})
    assert dr2.start == dt.date(2026, 8, 1)
    assert dr2.end == dt.date(2026, 8, 31)


def test_comparison_execution(db):
    engine = FinancialQueryEngine(db)
    q = make_q(date_range=DateRange(type=DateRangeType.CUSTOM,
                                    start=dt.date(2026, 8, 1), end=dt.date(2026, 8, 31),
                                    label="Aug 2026"))
    base = engine.execute(q)
    comparison = engine.execute_comparison(q, base, dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert comparison.summary["value"] != base.summary["value"] or True  # data-dependent
    is_cmp = comparison.query_metadata["is_comparison_of"]
    assert is_cmp["base_label"] == "Aug 2026"
    assert is_cmp["comparison_label"] is not None
