"""Query builder: validated FinancialQuery → SQLAlchemy select.

Every column, table, and operator referenced here comes from the ORM models —
never from strings produced by the LLM. This is the only path from a
FinancialQuery to SQL. Filter values are bound as parameters (SQLAlchemy
parametrized queries), which makes SQL injection structurally impossible.
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from ..models import Account, Bank, Transaction
from ..schemas.query import (
    Aggregation,
    DateRangeType,
    FinancialQuery,
    GroupByDimension,
    Metric,
    SortDirection,
)

_DIMENSION_TO_EXPR: dict[GroupByDimension, list] = {
    # (select expression, group-by expression) — a dimension may alias columns
    GroupByDimension.BANK: [Bank.bank_code, Bank.bank_name],
    GroupByDimension.ACCOUNT: [Account.account_id, Account.account_number],
    GroupByDimension.TRANSACTION_TYPE: [Transaction.transaction_type],
    GroupByDimension.MONTH: [sa.func.date(Transaction.transaction_date)],
}

_AGG_TO_FN = {
    Aggregation.SUM: sa.func.sum,
    Aggregation.AVG: sa.func.avg,
    Aggregation.MAX: sa.func.max,
    Aggregation.MIN: sa.func.min,
}


def _metric_expr(q: FinancialQuery):
    if q.metric == Metric.BALANCE:
        # balance metric is handled by dedicated balance queries
        raise ValueError("balance metric uses dedicated balance queries")
    agg = q.aggregation
    if agg == Aggregation.COUNT:
        return sa.func.count(sa.distinct(Transaction.transaction_id)).label("value")
    if agg == Aggregation.NONE:
        return sa.literal(1).label("_one")
    if agg not in _AGG_TO_FN:
        raise ValueError(f"aggregation '{agg.value}' not supported here")
    return _AGG_TO_FN[agg](Transaction.transaction_amount).label("value")


def _apply_filters(stmt, q: FinancialQuery):
    """WHERE clauses only. Joins are owned by _base_query."""
    f = q.filters
    if f.transaction_type:
        stmt = stmt.where(Transaction.transaction_type == f.transaction_type)
    if f.description_contains:
        # substring match, parameter-bound
        stmt = stmt.where(
            Transaction.description.ilike(f"%{f.description_contains}%")
        )
    if f.reference_id:
        stmt = stmt.where(
            Transaction.transaction_reference_id == f.reference_id
        )
    if f.utr_number:
        stmt = stmt.where(Transaction.utr_number == f.utr_number)
    if f.min_amount is not None:
        stmt = stmt.where(Transaction.transaction_amount >= f.min_amount)
    if f.max_amount is not None:
        stmt = stmt.where(Transaction.transaction_amount <= f.max_amount)
    if f.bank_code:
        stmt = stmt.where(Bank.bank_code == f.bank_code)
    elif f.bank_name:
        stmt = stmt.where(Bank.bank_name.ilike(f"%{f.bank_name}%"))
    if f.account_id:
        stmt = stmt.where(Transaction.account_id == f.account_id)
    return stmt


def _base_query(q: FinancialQuery, cols):
    """SELECT ... FROM transaction JOIN account JOIN bank.

    Joins live ONLY here so they can never be duplicated (a bug we hit in
    the previous iteration).
    """
    return (
        sa.select(*cols)
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.account_id)
        .join(Bank, Account.bank_code == Bank.bank_code)
    )


def _date_clause(q: FinancialQuery) -> list:
    """transaction_date is a TIMESTAMP; date_range bounds are dates.

    Comparing against dates-as-day-boundaries: the column is cast to DATE in
    the WHERE clause so a timestamp mid-day still matches its own day. On
    MySQL, DATE(timestamp) works natively; on SQLite the comparison between
    a 'YYYY-MM-DD HH:MM:SS' string and a 'YYYY-MM-DD' date also works
    lexicographically for >= the start bound, but NOT for <= end (a timestamp
    on the end day sorts after the bare date). We therefore compare against
    next-day-exclusive bounds instead — portable and index-friendly.
    """
    if q.date_range.type == DateRangeType.ALL_TIME or q.date_range.start is None:
        return []
    start = dt.datetime.combine(q.date_range.start, dt.time.min)
    end_exclusive = dt.datetime.combine(q.date_range.end, dt.time.min) + dt.timedelta(days=1)
    return [Transaction.transaction_date >= start,
            Transaction.transaction_date < end_exclusive]


def build_select(q: FinancialQuery) -> sa.Select:
    """Compile a validated FinancialQuery into a SELECT."""
    is_agg = q.aggregation != Aggregation.NONE

    if is_agg:
        cols = [_metric_expr(q)]
        group_exprs: list = []
        for dim in q.group_by:
            for i, expr in enumerate(_DIMENSION_TO_EXPR[dim]):
                if dim == GroupByDimension.BANK:
                    label = "bank_code" if i == 0 else "bank_name"
                elif dim == GroupByDimension.ACCOUNT:
                    label = "account_id" if i == 0 else "account_number_masked"
                else:
                    label = dim.value
                cols.append(expr.label(label))
                group_exprs.append(expr)
        stmt = _base_query(q, cols)
        stmt = stmt.where(*_date_clause(q))
        stmt = _apply_filters(stmt, q)
        if group_exprs:
            stmt = stmt.group_by(*group_exprs)
        if q.sort == SortDirection.DESC:
            stmt = stmt.order_by(sa.desc(sa.literal_column("value")))
        else:
            stmt = stmt.order_by(sa.asc(sa.literal_column("value")))
        if q.limit:
            stmt = stmt.limit(q.limit)
        return stmt

    # ----- record listing (aggregation = none) ------------------------------
    cols = [
        Transaction.transaction_id,
        Transaction.transaction_date,
        Transaction.transaction_type,
        Transaction.description,
        Transaction.transaction_amount,
        Transaction.transaction_reference_id,
        Transaction.utr_number,
        Account.account_number,        # masked downstream, never raw in answers
        Bank.bank_code,
        Bank.bank_name,
    ]
    stmt = _base_query(q, cols)
    stmt = stmt.where(*_date_clause(q))
    stmt = _apply_filters(stmt, q)
    # Sensible default ordering: biggest first for "largest transactions",
    # newest first otherwise.
    if q.filters.min_amount is not None and q.filters.max_amount is None:
        stmt = stmt.order_by(sa.desc(Transaction.transaction_amount))
    else:
        stmt = stmt.order_by(sa.desc(Transaction.transaction_date))
    stmt = stmt.limit(q.limit or 20)
    return stmt


# ---------------------------------------------------------------------------
# Balance / account / bank intents — these read account.available_balance,
# not transactions, so they get their own builders.
# ---------------------------------------------------------------------------

def _balance_base(cols):
    return (
        sa.select(*cols)
        .select_from(Account)
        .join(Bank, Account.bank_code == Bank.bank_code)
    )


def _bank_filter(stmt, f):
    """Bank filtering shared by account-side queries."""
    if f.bank_code:
        stmt = stmt.where(Bank.bank_code == f.bank_code)
    elif f.bank_name:
        stmt = stmt.where(Bank.bank_name.ilike(f"%{f.bank_name}%"))
    return stmt


def _ordered_by_value(stmt, q: FinancialQuery):
    if q.sort == SortDirection.DESC:
        return stmt.order_by(sa.desc(sa.literal_column("value")))
    return stmt.order_by(sa.asc(sa.literal_column("value")))


def build_balance_select(q: FinancialQuery) -> sa.Select:
    """account_balance / bank_balance intents.

    Shape depends on filters + group_by:
      - filters.account_id set        → that one account
      - group_by=[bank]               → per-bank totals ("which bank holds most")
      - group_by=[account]            → per-account ("which account has highest balance")
      - otherwise                     → SUM over all (filtered) accounts
    """
    f = q.filters
    if f.account_id:
        return _balance_base([
            Account.account_id,
            Account.account_number,
            Account.available_balance,
            Account.program_id,
            Bank.bank_code,
            Bank.bank_name,
        ]).where(Account.account_id == f.account_id)
    if GroupByDimension.BANK in q.group_by:
        stmt = _balance_base([
            Bank.bank_code,
            Bank.bank_name,
            sa.func.sum(Account.available_balance).label("value"),
            sa.func.count(sa.distinct(Account.account_id)).label("account_count"),
        ]).group_by(Bank.bank_code, Bank.bank_name)
        stmt = _bank_filter(stmt, f)
        return _ordered_by_value(stmt, q).limit(q.limit or 10)
    if GroupByDimension.ACCOUNT in q.group_by:
        stmt = _balance_base([
            Account.account_id,
            Account.account_number,
            Account.available_balance.label("value"),
            Account.program_id,
            Bank.bank_code,
            Bank.bank_name,
        ])
        stmt = _bank_filter(stmt, f)
        return _ordered_by_value(stmt, q).limit(q.limit or 10)
    stmt = _balance_base([
        sa.func.sum(Account.available_balance).label("value"),
        sa.func.count(sa.distinct(Account.account_id)).label("account_count"),
    ])
    return _bank_filter(stmt, f)


def build_account_list(q: FinancialQuery) -> sa.Select:
    stmt = _balance_base([
        Account.account_id,
        Account.account_number,
        Account.available_balance,
        Account.program_id,
        Bank.bank_code,
        Bank.bank_name,
    ])
    stmt = _bank_filter(stmt, q.filters)
    if q.sort == SortDirection.DESC:
        stmt = stmt.order_by(sa.desc(Account.available_balance))
    else:
        stmt = stmt.order_by(sa.asc(Account.available_balance))
    return stmt.limit(q.limit or 20)


def build_account_count_by_bank(q: FinancialQuery) -> sa.Select:
    stmt = _balance_base([
        Bank.bank_code,
        Bank.bank_name,
        sa.func.count(sa.distinct(Account.account_id)).label("value"),
    ]).group_by(Bank.bank_code, Bank.bank_name)
    stmt = _bank_filter(stmt, q.filters)
    return _ordered_by_value(stmt, q).limit(q.limit or 10)


def build_monthly_trend(q: FinancialQuery) -> sa.Select:
    """monthly_trend: aggregate grouped by calendar month.

    Month is extracted as (year, month) two integer columns — portable across
    MySQL, SQLite, and PostgreSQL (unlike DATE_FORMAT / strftime / to_char).
    The engine renders the 'YYYY-MM' label.
    """
    agg = q.aggregation
    if agg == Aggregation.COUNT:
        metric = sa.func.count(sa.distinct(Transaction.transaction_id)).label("value")
    elif agg in _AGG_TO_FN:
        metric = _AGG_TO_FN[agg](Transaction.transaction_amount).label("value")
    else:
        raise ValueError("monthly_trend requires sum/count/avg/max/min aggregation")
    year = sa.extract("year", Transaction.transaction_date)
    month = sa.extract("month", Transaction.transaction_date)
    year_l = year.label("txn_year")
    month_l = month.label("txn_month")
    stmt = _base_query(q, [year_l, month_l, metric])
    stmt = stmt.where(*_date_clause(q))
    stmt = _apply_filters(stmt, q)
    stmt = stmt.group_by(year, month)
    return stmt.order_by(sa.asc(year), sa.asc(month))
