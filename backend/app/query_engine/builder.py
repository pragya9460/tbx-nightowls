"""Query builder: validated FinancialQuery → SQLAlchemy select.

Every column, table, and operator referenced here comes from the ORM models —
never from strings produced by the LLM. This is the only path from a
FinancialQuery to SQL.
"""
from __future__ import annotations

import sqlalchemy as sa

from ..models import Reconciliation, Transaction, Vendor, VendorPayout
from ..schemas.query import Aggregation, DateRangeType, FinancialQuery, GroupByDimension, Metric

_DIMENSION_TO_COLUMN: dict[GroupByDimension, sa.Column] = {
    GroupByDimension.VENDOR: Vendor.vendor_name,
    GroupByDimension.VENDOR_CATEGORY: Vendor.category,
    GroupByDimension.TRANSACTION_CATEGORY: Transaction.category,
    GroupByDimension.ACCOUNT: Transaction.account,
    GroupByDimension.PAYOUT_STATUS: VendorPayout.status,
    GroupByDimension.RECONCILIATION_STATUS: Transaction.reconciliation_status,
    GroupByDimension.MONTH: sa.func.date_trunc("month", VendorPayout.payout_date),
}

_METRIC_TO_EXPR: dict[Metric, dict[Aggregation, sa.Column]] = {
    Metric.PAYOUT_AMOUNT: {
        Aggregation.SUM: VendorPayout.amount,
        Aggregation.AVG: VendorPayout.amount,
        Aggregation.MAX: VendorPayout.amount,
        Aggregation.MIN: VendorPayout.amount,
    },
    Metric.PAYOUT_COUNT: {
        Aggregation.COUNT: VendorPayout.payout_id,
        Aggregation.NONE: VendorPayout.payout_id,
    },
    Metric.TRANSACTION_AMOUNT: {
        Aggregation.SUM: Transaction.amount,
        Aggregation.AVG: Transaction.amount,
        Aggregation.MAX: Transaction.amount,
        Aggregation.MIN: Transaction.amount,
    },
    Metric.TRANSACTION_COUNT: {
        Aggregation.COUNT: Transaction.transaction_id,
        Aggregation.NONE: Transaction.transaction_id,
    },
}


def _base_table(q: FinancialQuery) -> sa.Table:
    if q.metric in (Metric.PAYOUT_AMOUNT, Metric.PAYOUT_COUNT):
        return VendorPayout.__table__
    return Transaction.__table__


def _apply_filters(stmt, q: FinancialQuery):
    """Apply joins + WHERE clauses. This is the ONLY place joins are added —
    callers must pass a stmt with select_from already set, without a Vendor
    join of its own."""
    f = q.filters
    if q.metric in (Metric.PAYOUT_AMOUNT, Metric.PAYOUT_COUNT):
        if f.vendor_id:
            stmt = stmt.where(VendorPayout.vendor_id == f.vendor_id)
        if f.vendor_name:
            stmt = stmt.where(sa.func.lower(Vendor.vendor_name) == f.vendor_name.lower())
        if f.vendor_category:
            stmt = stmt.where(sa.func.lower(Vendor.category) == f.vendor_category.lower())
        if f.payout_status:
            stmt = stmt.where(VendorPayout.status == f.payout_status)
    else:
        if f.vendor_id:
            stmt = stmt.where(Transaction.vendor_id == f.vendor_id)
        if f.vendor_name:
            stmt = stmt.where(sa.func.lower(Vendor.vendor_name) == f.vendor_name.lower())
        if f.vendor_category:
            stmt = stmt.where(sa.func.lower(Vendor.category) == f.vendor_category.lower())
        if f.reconciliation_status:
            stmt = stmt.where(Transaction.reconciliation_status == f.reconciliation_status)
        if f.transaction_category:
            stmt = stmt.where(sa.func.lower(Transaction.category) == f.transaction_category.lower())
        if f.account:
            stmt = stmt.where(sa.func.lower(Transaction.account) == f.account.lower())
        if f.transaction_type:
            stmt = stmt.where(Transaction.transaction_type == f.transaction_type)
    return stmt


def _base_query(q: FinancialQuery, cols):
    """Base SELECT with the correct join, ready for filters/date-ranges."""
    if q.metric in (Metric.PAYOUT_AMOUNT, Metric.PAYOUT_COUNT):
        return sa.select(*cols).select_from(VendorPayout).join(
            Vendor, VendorPayout.vendor_id == Vendor.vendor_id
        )
    return sa.select(*cols).select_from(Transaction).outerjoin(
        Vendor, Transaction.vendor_id == Vendor.vendor_id
    )


def _date_clause(q: FinancialQuery, dr):
    t = _base_table(q)
    date_col = VendorPayout.payout_date if t is VendorPayout.__table__ else Transaction.transaction_date
    if dr.type == DateRangeType.ALL_TIME or dr.start is None:
        return []
    return [date_col >= dr.start, date_col <= dr.end]


def _metric_expr(q: FinancialQuery):
    agg = q.aggregation
    options = _METRIC_TO_EXPR[q.metric]
    if agg == Aggregation.COUNT:
        return sa.func.count(options[Aggregation.COUNT]).label("value")
    if agg == Aggregation.NONE:
        return sa.literal(1).label("_one")  # placeholder; list intents don't aggregate
    if agg not in options:
        raise ValueError(f"aggregation '{agg.value}' not supported for metric '{q.metric.value}'")
    col = options[agg]
    fn = {Aggregation.SUM: sa.func.sum, Aggregation.AVG: sa.func.avg,
          Aggregation.MAX: sa.func.max, Aggregation.MIN: sa.func.min}[agg]
    return fn(col).label("value")


def _group_expr(q: FinancialQuery):
    if not q.group_by:
        return []
    return [
        _DIMENSION_TO_COLUMN[d].label(d.value if d != GroupByDimension.VENDOR else "vendor_name")
        for d in q.group_by
    ]


def build_select(q: FinancialQuery) -> sa.Select:
    """Compile a validated FinancialQuery into a SELECT. Raises ValueError for
    combinations the semantic layer does not support."""
    t = _base_table(q)

    if q.intent.value == "unreconciled_list":
        return _build_unreconciled_list(q)

    is_agg = q.aggregation != Aggregation.NONE
    cols: list = []
    if is_agg:
        cols.append(_metric_expr(q))
        cols.extend(_group_expr(q))
    else:
        # record listing
        if t is VendorPayout.__table__:
            cols = [
                VendorPayout.payout_id, VendorPayout.payout_date,
                Vendor.vendor_name, VendorPayout.amount, VendorPayout.status,
            ]
        else:
            cols = [
                Transaction.transaction_id, Transaction.transaction_date,
                Vendor.vendor_name, Transaction.amount, Transaction.category,
                Transaction.account, Transaction.reconciliation_status,
                Transaction.description,
            ]
    stmt = _base_query(q, cols)
    stmt = _apply_filters(stmt, q)

    if q.date_range.type != DateRangeType.ALL_TIME and q.date_range.start:
        date_col = (VendorPayout.payout_date
                    if t is VendorPayout.__table__
                    else Transaction.transaction_date)
        stmt = stmt.where(date_col >= q.date_range.start,
                          date_col <= q.date_range.end)

    if is_agg:
        if q.group_by:
            stmt = stmt.group_by(*[c.name for c in _group_expr(q)]) \
                       .order_by(sa.desc(sa.literal_column("value")))
        else:
            stmt = stmt.order_by(sa.desc(sa.literal_column("value")))
        if q.limit:
            stmt = stmt.limit(q.limit)
    else:
        if q.intent.value in ("unreconciled_list",):
            stmt = stmt.order_by(sa.desc(Transaction.transaction_date))
        elif t is VendorPayout.__table__:
            stmt = stmt.order_by(sa.desc(VendorPayout.payout_date))
        else:
            stmt = stmt.order_by(sa.desc(Transaction.transaction_date))
        stmt = stmt.limit(q.limit or 50)

    return stmt


def _build_unreconciled_list(q: FinancialQuery) -> sa.Select:
    stmt = sa.select(
        Transaction.transaction_id, Transaction.transaction_date,
        Vendor.vendor_name, Transaction.amount, Transaction.category,
        Transaction.account, Transaction.reconciliation_status,
        Transaction.description,
    ).select_from(Transaction).outerjoin(Vendor, Transaction.vendor_id == Vendor.vendor_id)
    if q.filters.reconciliation_status:
        stmt = stmt.where(Transaction.reconciliation_status == q.filters.reconciliation_status)
    else:
        stmt = stmt.where(Transaction.reconciliation_status == "unreconciled")
    if q.date_range.type != DateRangeType.ALL_TIME and q.date_range.start:
        stmt = stmt.where(Transaction.transaction_date >= q.date_range.start,
                          Transaction.transaction_date <= q.date_range.end)
    if q.limit:
        stmt = stmt.limit(q.limit)
    return stmt.order_by(sa.desc(Transaction.transaction_date))
