"""Query-engine tests: aggregation, grouping, listing, balance intents.

Expected values are computed independently against the same MySQL connection
— the tests verify the engine against ground truth, not against itself.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.query_engine.mysql_builder import compile_query
from app.schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    GroupByDimension,
    Intent,
    Metric,
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


def _scalar(engine, sql: str, params: dict | None = None):
    with engine._con.cursor() as cur:
        cur.execute(sql, params or {})
        row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _fetchall(engine, sql: str, params: dict | None = None):
    with engine._con.cursor() as cur:
        cur.execute(sql, params or {})
        rows = cur.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [tuple(r.values()) for r in rows]
    return list(rows)


# ---------------------------------------------------------------------------
# Compiler safety
# ---------------------------------------------------------------------------

def test_compile_emits_select_only():
    compiled = compile_query(make_q())
    assert compiled.sql.lstrip().upper().startswith("SELECT")
    assert "INSERT" not in compiled.sql.upper()
    assert '`transaction`' in compiled.sql


# ---------------------------------------------------------------------------
# Aggregation correctness against MySQL ground truth
# ---------------------------------------------------------------------------

def test_debit_sum_aug_2026(duck_engine):
    result = duck_engine.execute(make_q(filters={"transaction_type": "debit"}))
    expected = _scalar(
        duck_engine,
        """
        SELECT COALESCE(SUM(t.transaction_amount), 0)
        FROM `transaction` t
        WHERE t.transaction_type = 'debit'
          AND CAST(t.transaction_date AS DATE) >= '2026-08-01'
          AND CAST(t.transaction_date AS DATE) <= '2026-08-31'
        """,
    )
    assert result.summary["value"] == pytest.approx(float(expected), rel=1e-6)
    expected_n = _scalar(
        duck_engine,
        """
        SELECT COUNT(DISTINCT t.transaction_id)
        FROM `transaction` t
        WHERE t.transaction_type = 'debit'
          AND CAST(t.transaction_date AS DATE) >= '2026-08-01'
          AND CAST(t.transaction_date AS DATE) <= '2026-08-31'
        """,
    )
    assert result.summary["record_count"] == expected_n
    assert result.query_metadata["backend"] == "mysql"
    assert "sql" in result.query_metadata


def test_credit_count_all_time(duck_engine):
    result = duck_engine.execute(FinancialQuery.model_validate({
        "intent": "transaction_summary",
        "metric": "transaction_count",
        "aggregation": "count",
        "filters": {"transaction_type": "credit"},
        "date_range": {"type": "all_time"},
    }))
    expected = _scalar(
        duck_engine,
        """SELECT COUNT(DISTINCT transaction_id) FROM `transaction`
           WHERE transaction_type = 'credit'""",
    )
    assert result.summary["value"] == expected


def test_avg_max_min(duck_engine):
    for agg in (Aggregation.AVG, Aggregation.MAX, Aggregation.MIN):
        r = duck_engine.execute(make_q(
            aggregation=agg, filters={"transaction_type": "debit"},
        ))
        fn = {Aggregation.AVG: "AVG", Aggregation.MAX: "MAX",
              Aggregation.MIN: "MIN"}[agg]
        expected = _scalar(
            duck_engine,
            f"""
            SELECT {fn}(t.transaction_amount)
            FROM `transaction` t
            WHERE t.transaction_type = 'debit'
              AND CAST(t.transaction_date AS DATE) >= '2026-08-01'
              AND CAST(t.transaction_date AS DATE) <= '2026-08-31'
            """,
        )
        assert r.summary["value"] == pytest.approx(float(expected), rel=1e-6)


def test_group_by_bank_matches_orm(duck_engine):
    q = make_q(group_by=[GroupByDimension.BANK])
    result = duck_engine.execute(q)
    rows = _fetchall(
        duck_engine,
        """
        SELECT b.bank_code, SUM(t.transaction_amount)
        FROM `transaction` t
        JOIN account a ON t.account_id = a.account_id
        JOIN bank b ON a.bank_code = b.bank_code
        WHERE CAST(t.transaction_date AS DATE) >= '2026-08-01'
          AND CAST(t.transaction_date AS DATE) <= '2026-08-31'
        GROUP BY b.bank_code
        """
    )
    expected = {code: total for code, total in rows}
    got = {b["bank_code"]: b["value"] for b in result.breakdown}
    for code, total in expected.items():
        assert got[code] == pytest.approx(float(total), rel=1e-6)


def test_group_by_transaction_type(duck_engine):
    q = make_q(group_by=[GroupByDimension.TRANSACTION_TYPE],
               date_range={"type": "all_time"})
    result = duck_engine.execute(q)
    types = {b["transaction_type"]: b["value"] for b in result.breakdown}
    assert set(types) == {"debit", "credit"}
    expected_debit = _scalar(
        duck_engine,
        """SELECT SUM(transaction_amount) FROM `transaction`
           WHERE transaction_type = 'debit'""",
    )
    assert types["debit"] == pytest.approx(float(expected_debit), rel=1e-6)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_largest_transactions_sorted_desc(duck_engine):
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE,
               filters={"transaction_type": "debit", "min_amount": 10000},
               limit=10)
    result = duck_engine.execute(q)
    amounts = [r["transaction_amount"] for r in result.records]
    assert amounts == sorted(amounts, reverse=True)
    assert all(a >= 10000 for a in amounts)
    assert len(result.records) <= 10


def test_list_records_count_exceeds_shown(duck_engine):
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE, limit=5)
    result = duck_engine.execute(q)
    assert len(result.records) <= 5
    expected_all = _scalar(
        duck_engine,
        """
        SELECT COUNT(DISTINCT transaction_id) FROM `transaction`
        WHERE CAST(transaction_date AS DATE) >= '2026-08-01'
          AND CAST(transaction_date AS DATE) <= '2026-08-31'
        """,
    )
    assert result.summary["record_count"] == expected_all


def test_description_search(duck_engine):
    sample = _scalar(duck_engine, 'SELECT description FROM `transaction` LIMIT 1')
    needle = sample.split()[0]
    q = make_q(intent=Intent.TRANSACTION_LIST, aggregation=Aggregation.NONE,
               filters={"description_contains": needle}, limit=20)
    result = duck_engine.execute(q)
    assert len(result.records) >= 1
    for r in result.records:
        assert needle.lower() in (r["description"] or "").lower()


# ---------------------------------------------------------------------------
# Balance / account / bank intents
# ---------------------------------------------------------------------------

def test_total_balance_matches_orm(duck_engine):
    q = make_q(intent=Intent.ACCOUNT_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM,
               date_range={"type": "all_time"})
    result = duck_engine.execute(q)
    expected = _scalar(duck_engine, "SELECT SUM(available_balance) FROM account")
    assert result.summary["value"] == pytest.approx(float(expected), rel=1e-6)
    expected_n = _scalar(duck_engine, "SELECT COUNT(account_id) FROM account")
    assert result.summary["record_count"] == expected_n


def test_bank_highest_balance(duck_engine):
    q = make_q(intent=Intent.BANK_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM, group_by=[GroupByDimension.BANK],
               date_range={"type": "all_time"}, limit=10)
    result = duck_engine.execute(q)
    values = [b["value"] for b in result.breakdown]
    assert values == sorted(values, reverse=True)
    rows = _fetchall(
        duck_engine,
        """
        SELECT b.bank_code, SUM(a.available_balance)
        FROM bank b JOIN account a ON a.bank_code = b.bank_code
        GROUP BY b.bank_code
        """
    )
    expected = {code: total for code, total in rows}
    for b in result.breakdown:
        assert b["value"] == pytest.approx(float(expected[b["bank_code"]]), rel=1e-6)


def test_single_account_balance(duck_engine):
    row = _fetchall(
        duck_engine,
        "SELECT account_id, available_balance FROM account LIMIT 1"
    )[0]
    q = make_q(intent=Intent.ACCOUNT_BALANCE, metric=Metric.BALANCE,
               aggregation=Aggregation.SUM,
               filters={"account_id": row[0]},
               date_range={"type": "all_time"})
    result = duck_engine.execute(q)
    assert result.summary["value"] == pytest.approx(float(row[1]), rel=1e-6)


def test_account_count_by_bank(duck_engine):
    q = make_q(intent=Intent.BANK_ACCOUNT_COUNT, metric=Metric.TRANSACTION_COUNT,
               aggregation=Aggregation.COUNT, date_range={"type": "all_time"})
    result = duck_engine.execute(q)
    rows = _fetchall(
        duck_engine,
        """
        SELECT b.bank_code, COUNT(a.account_id)
        FROM bank b JOIN account a ON a.bank_code = b.bank_code
        GROUP BY b.bank_code
        """
    )
    expected = {code: n for code, n in rows}
    got = {b["bank_code"]: b["value"] for b in result.breakdown}
    assert got == expected


# ---------------------------------------------------------------------------
# Monthly trend + reference lookup
# ---------------------------------------------------------------------------

def test_monthly_trend_peak(duck_engine):
    q = make_q(intent=Intent.MONTHLY_TREND, group_by=[GroupByDimension.MONTH],
               date_range={"type": "all_time"})
    result = duck_engine.execute(q)
    assert result.summary["peak_month"] is not None
    rows = _fetchall(
        duck_engine,
        """
        SELECT CONCAT(YEAR(transaction_date), '-', LPAD(MONTH(transaction_date), 2, '0')),
               SUM(transaction_amount)
        FROM `transaction`
        GROUP BY 1
        """
    )
    expected_peak = max(rows, key=lambda r: r[1])[0]
    assert result.summary["peak_month"] == expected_peak
    months = [b["month"] for b in result.breakdown]
    assert months == sorted(months)


def test_reference_lookup_exact(duck_engine):
    row = _fetchall(
        duck_engine,
        """
        SELECT transaction_reference_id FROM `transaction`
        WHERE transaction_reference_id IS NOT NULL LIMIT 1
        """
    )[0]
    q = make_q(intent=Intent.REFERENCE_LOOKUP, metric=Metric.TRANSACTION_COUNT,
               aggregation=Aggregation.NONE,
               filters={"reference_id": row[0]},
               date_range={"type": "all_time"}, limit=10)
    result = duck_engine.execute(q)
    assert result.summary["record_count"] >= 1
    assert all(r["transaction_reference_id"] == row[0] for r in result.records)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def test_comparison_jul_aug_2026(duck_engine):
    q = make_q(
        intent=Intent.COMPARISON,
        filters={"transaction_type": "debit"},
        comparison={"against": "previous_period"},
    )
    result = duck_engine.execute(q)
    cmp_result = duck_engine.execute_comparison(
        q, result, dt.date(2026, 7, 1), dt.date(2026, 7, 31),
    )
    expected_jul = _scalar(
        duck_engine,
        """
        SELECT COALESCE(SUM(t.transaction_amount), 0)
        FROM `transaction` t
        WHERE t.transaction_type = 'debit'
          AND CAST(t.transaction_date AS DATE) >= '2026-07-01'
          AND CAST(t.transaction_date AS DATE) <= '2026-07-31'
        """,
    )
    assert cmp_result.summary["value"] == pytest.approx(float(expected_jul), rel=1e-6)
    assert cmp_result.query_metadata["is_comparison_of"] is True
    assert cmp_result.query_metadata["date_range"]["label"] == "Jul 2026"


def test_comparison_month_before_previous_resolution():
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "month_before_previous"})
    assert (dr.start, dr.end) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


def test_comparison_execution(duck_engine):
    q = make_q(date_range=DateRange(
        type=DateRangeType.CUSTOM,
        start=dt.date(2026, 8, 1),
        end=dt.date(2026, 8, 31),
        label="Aug 2026",
    ))
    base = duck_engine.execute(q)
    comparison = duck_engine.execute_comparison(
        q, base, dt.date(2026, 7, 1), dt.date(2026, 7, 31)
    )
    assert comparison.query_metadata["is_comparison_of"] is True
    assert comparison.query_metadata["date_range"]["label"] == "Jul 2026"


def test_named_month_comparison_not_rewritten(duck_engine):
    """July vs August must keep August bounds — not collapse to June."""
    from app.schemas.query import ComparisonSpec, Intent

    q = make_q(
        intent=Intent.COMPARISON,
        filters={"transaction_type": "debit"},
        date_range=DateRange(
            type=DateRangeType.CALENDAR_MONTH,
            start=dt.date(2026, 7, 1),
            end=dt.date(2026, 7, 31),
            label="Jul 2026",
        ),
        comparison=ComparisonSpec(against="named_month", month="august"),
    )
    base = duck_engine.execute(q)
    cmp_result = duck_engine.execute_comparison(
        q, base, dt.date(2026, 8, 1), dt.date(2026, 8, 31)
    )
    assert cmp_result.query_metadata["date_range"]["label"] == "Aug 2026"


def test_previous_period_full_calendar_month():
    from app.schemas.query import previous_period

    dr = DateRange(
        type=DateRangeType.CALENDAR_MONTH,
        start=dt.date(2026, 7, 1),
        end=dt.date(2026, 7, 31),
        label="Jul 2026",
    )
    pp = previous_period(dr)
    assert (pp.start, pp.end) == (dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    assert pp.label == "Jun 2026"


def test_count_total(duck_engine):
    assert duck_engine.count_total("bank") == 10
    assert duck_engine.count_total("account") == 12
    assert duck_engine.count_total("transaction") == 600


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
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "calendar_month", "month": "june"})
    assert dr.start == dt.date(2026, 6, 1)
    dr = resolve_date_range(dt.date(2026, 9, 5), {"type": "calendar_month", "month": "december"})
    assert dr.start == dt.date(2025, 12, 1)


def test_date_range_start_after_end_rejected():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_summary", "metric": "transaction_amount",
            "aggregation": "sum", "date_range": {
                "type": "custom", "start": "2026-08-31", "end": "2026-08-01"},
        })
