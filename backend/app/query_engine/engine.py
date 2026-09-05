"""Deterministic financial query engine.

Executes a validated FinancialQuery against PostgreSQL and returns a typed
QueryResult. Every value in the result comes from the database — nothing is
computed by an LLM, ever.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..schemas.query import Aggregation, DateRangeType, FinancialQuery
from .builder import _base_table, build_select


@dataclass
class QueryResult:
    summary: dict = field(default_factory=dict)
    breakdown: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    query_metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "breakdown": self.breakdown,
            "records": self.records,
            "query_metadata": self.query_metadata,
        }


def _decimal_to_float(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "quantize"):  # Decimal
        return float(v)
    return v


def _rows_to_dicts(result) -> list[dict]:
    keys = list(result.keys())
    return [
        {k: _decimal_to_float(v) for k, v in zip(keys, row)}
        for row in result
    ]


class FinancialQueryEngine:
    def __init__(self, db: Session):
        self.db = db

    def execute(self, q: FinancialQuery) -> QueryResult:
        stmt = build_select(q)
        raw = self.db.execute(stmt)
        rows = _rows_to_dicts(raw)

        meta = {
            "intent": q.intent.value,
            "metric": q.metric.value,
            "aggregation": q.aggregation.value,
            "filters": {k: v for k, v in q.filters.model_dump().items() if v is not None},
            "date_range": q.date_range.model_dump(mode="json", exclude_none=True),
            "group_by": [g.value for g in q.group_by],
            "limit": q.limit,
        }

        if q.aggregation == Aggregation.NONE:
            return QueryResult(
                summary={"record_count": self._count_matched(q)},
                records=rows,
                query_metadata=meta,
            )

        # True count of matched source records (independent of grouping/limit).
        matched = self._count_matched(q)

        # Aggregation path: single row (no group_by) or grouped breakdown.
        if q.group_by:
            if q.limit is not None and len(rows) == q.limit:
                # Limited grouped query: rows only cover the top N groups, so
                # their sum is NOT the period total. Recompute over all
                # matched records (no limit) so the stated total is honest.
                total_q = q.model_copy(deep=True, update={"limit": None})
                total_rows = _rows_to_dicts(self.db.execute(build_select(total_q)))
                total = sum(r.get("value", 0) or 0 for r in total_rows)
            else:
                total = sum(r.get("value", 0) or 0 for r in rows)
            return QueryResult(
                summary={"value": total, "record_count": matched},
                breakdown=rows,
                query_metadata=meta,
            )
        value = rows[0].get("value") if rows else 0
        return QueryResult(
            summary={
                "value": value if value is not None else 0,
                "record_count": matched,
            },
            query_metadata=meta,
        )

    def _count_matched(self, q: FinancialQuery) -> int:
        """COUNT(*) over the same filters/date-range, pre-grouping/pre-limit."""
        import sqlalchemy as sa

        from ..models import Transaction, VendorPayout
        from .builder import _apply_filters, _base_query

        t = _base_table(q)
        stmt = _base_query(q, [sa.func.count()])
        stmt = _apply_filters(stmt, q)
        if q.date_range.type != DateRangeType.ALL_TIME and q.date_range.start:
            date_col = (VendorPayout.payout_date
                        if t is VendorPayout.__table__
                        else Transaction.transaction_date)
            stmt = stmt.where(date_col >= q.date_range.start,
                              date_col <= q.date_range.end)
        return self.db.execute(stmt).scalar_one() or 0

    def execute_comparison(self, q: FinancialQuery, base_result: QueryResult,
                           previous_range_start: dt.date, previous_range_end: dt.date) -> QueryResult:
        """Run the same query over the comparison date range."""
        from copy import deepcopy

        q2 = deepcopy(q)
        q2.date_range.start = previous_range_start
        q2.date_range.end = previous_range_end
        q2.date_range.type = DateRangeType.CUSTOM
        # Re-label so the comparison answer names the *previous* period.
        q2.date_range.label = (
            f"{previous_range_start.strftime('%b %Y')}"
            if previous_range_start.day == 1
            and (previous_range_end + dt.timedelta(days=1)).day == 1
            and (previous_range_end - previous_range_start).days >= 27
            else f"{previous_range_start.isoformat()} to {previous_range_end.isoformat()}"
        )
        result = self.execute(q2)
        result.query_metadata["is_comparison_of"] = {
            "base_label": base_result.query_metadata["date_range"].get("label"),
            "comparison_label": q2.date_range.label,
        }
        return result

    def count_total(self, table: str) -> int:
        """Sanity helper for /health and tests."""
        allowed = {"transactions": "transactions", "vendor_payouts": "vendor_payouts",
                   "vendors": "vendors", "reconciliation": "reconciliation"}
        if table not in allowed:
            raise ValueError("unsupported table")
        return self.db.execute(text(f"SELECT COUNT(*) FROM {allowed[table]}")).scalar_one()
