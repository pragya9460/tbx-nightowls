"""Deterministic financial query engine.

Executes a validated FinancialQuery against the database and returns a typed
QueryResult. Every value in the result comes from SQL — nothing is computed
by an LLM, ever. Sensitive fields (account_number, utr_number) are masked
HERE, at the engine boundary, so no downstream code can accidentally leak
them.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    Intent,
    Metric,
)
from . import builder
from .builder import build_select


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
        return v.isoformat(sep=" ")
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


# ---------------------------------------------------------------------------
# Sensitive-field masking (spec §13). Applied in the engine, the single
# choke point every query result passes through.
# ---------------------------------------------------------------------------

def mask_account_number(acc: str | None) -> str | None:
    if not acc:
        return acc
    return f"XXXXX{acc[-4:]}"


def mask_utr(utr: str | None) -> str | None:
    if not utr:
        return utr
    if len(utr) <= 6:
        return utr[:2] + "***"
    return utr[:4] + "***" + utr[-2:]


def _mask_record(record: dict) -> dict:
    out = dict(record)
    if "account_number" in out:
        out["account_number"] = mask_account_number(out.get("account_number"))
    if "account_number_masked" in out:
        out["account_number_masked"] = mask_account_number(out.get("account_number_masked"))
    if "utr_number" in out:
        out["utr_number"] = mask_utr(out.get("utr_number"))
    return out


class FinancialQueryEngine:
    def __init__(self, db: Session):
        self.db = db

    def execute(self, q: FinancialQuery) -> QueryResult:
        if q.intent in (Intent.ACCOUNT_BALANCE, Intent.BANK_BALANCE):
            return self._execute_balance(q)
        if q.intent == Intent.ACCOUNT_LIST:
            return self._execute_account_list(q)
        if q.intent == Intent.BANK_ACCOUNT_COUNT:
            return self._execute_bank_account_count(q)
        if q.intent == Intent.MONTHLY_TREND:
            return self._execute_monthly_trend(q)
        if q.intent == Intent.REFERENCE_LOOKUP:
            return self._execute_reference_lookup(q)
        return self._execute_transaction_query(q)

    # ----- transaction intents ----------------------------------------------

    def _execute_transaction_query(self, q: FinancialQuery) -> QueryResult:
        stmt = build_select(q)
        rows = _rows_to_dicts(self.db.execute(stmt))

        meta = self._meta(q)
        if q.aggregation == Aggregation.NONE:
            # Listing: record_count = true matched count (pre-limit).
            matched = self._count_matched(q)
            return QueryResult(
                summary={"record_count": matched},
                records=[_mask_record(r) for r in rows],
                query_metadata=meta,
            )

        matched = self._count_matched(q)

        if q.group_by:
            # Grouped: the visible rows may be limited (top N). The summary
            # total must always cover ALL matched records, not just the shown
            # ones — recompute without limit when the result was truncated.
            total = sum(r.get("value", 0) or 0 for r in rows)
            if q.limit is not None and len(rows) == q.limit:
                total_q = q.model_copy(deep=True, update={"limit": None})
                all_rows = _rows_to_dicts(self.db.execute(build_select(total_q)))
                total = sum(r.get("value", 0) or 0 for r in all_rows)
            return QueryResult(
                summary={"value": total, "record_count": matched},
                breakdown=rows,
                query_metadata=meta,
            )

        value = rows[0].get("value") if rows else 0
        return QueryResult(
            summary={"value": value if value is not None else 0,
                     "record_count": matched},
            query_metadata=meta,
        )

    def _execute_reference_lookup(self, q: FinancialQuery) -> QueryResult:
        rows = _rows_to_dicts(self.db.execute(build_select(q)))
        return QueryResult(
            summary={"record_count": len(rows)},
            records=[_mask_record(r) for r in rows],
            query_metadata=self._meta(q),
        )

    def _execute_monthly_trend(self, q: FinancialQuery) -> QueryResult:
        rows = _rows_to_dicts(self.db.execute(builder.build_monthly_trend(q)))
        # Render YYYY-MM labels from the extracted year/month ints.
        breakdown = [
            {
                "month": f"{int(r['txn_year']):04d}-{int(r['txn_month']):02d}",
                "value": r["value"],
            }
            for r in rows
        ]
        matched = self._count_matched(q)
        peak = max(breakdown, key=lambda r: (r["value"] or 0)) if breakdown else None
        return QueryResult(
            summary={
                "value": peak["value"] if peak else 0,
                "record_count": matched,
                "peak_month": peak["month"] if peak else None,
                "months": len(breakdown),
            },
            breakdown=breakdown,
            query_metadata=self._meta(q),
        )

    # ----- balance / account / bank intents ----------------------------------

    def _execute_balance(self, q: FinancialQuery) -> QueryResult:
        rows = _rows_to_dicts(self.db.execute(builder.build_balance_select(q)))
        masked = [_mask_record(r) for r in rows]

        if q.filters.account_id:
            if not masked:
                return QueryResult(summary={"value": 0, "record_count": 0,
                                            "not_found": True},
                                   query_metadata=self._meta(q))
            r = masked[0]
            return QueryResult(
                summary={"value": r["available_balance"], "record_count": 1},
                breakdown=masked,
                query_metadata=self._meta(q),
            )
        if q.group_by:
            top = masked[0] if masked else None
            return QueryResult(
                summary={
                    "value": top["value"] if top else 0,
                    "record_count": sum(r.get("account_count", 1) for r in masked),
                },
                breakdown=masked,
                query_metadata=self._meta(q),
            )
        total = masked[0] if masked else {"value": 0, "account_count": 0}
        return QueryResult(
            summary={"value": total.get("value") or 0,
                     "record_count": total.get("account_count") or 0},
            query_metadata=self._meta(q),
        )

    def _execute_account_list(self, q: FinancialQuery) -> QueryResult:
        rows = _rows_to_dicts(self.db.execute(builder.build_account_list(q)))
        return QueryResult(
            summary={"record_count": len(rows),
                     "value": sum(r["available_balance"] for r in rows)},
            records=[_mask_record(r) for r in rows],
            query_metadata=self._meta(q),
        )

    def _execute_bank_account_count(self, q: FinancialQuery) -> QueryResult:
        rows = _rows_to_dicts(self.db.execute(builder.build_account_count_by_bank(q)))
        return QueryResult(
            summary={
                "value": sum(r["value"] for r in rows),
                "record_count": len(rows),
            },
            breakdown=rows,
            query_metadata=self._meta(q),
        )

    # ----- shared -------------------------------------------------------------

    def count_total(self, table: str) -> int:
        """Row count for a table — health endpoint + eval grounding only.
        Allowlisted table names; no user input reaches this."""
        import sqlalchemy as sa

        from ..models import Account, Bank, Transaction

        allowed = {
            "bank": Bank,
            "account": Account,
            "transaction": Transaction,
        }
        model = allowed.get(table)
        if model is None:
            raise ValueError(f"table '{table}' not allowlisted")
        return int(self.db.execute(sa.select(sa.func.count()).select_from(model)).scalar() or 0)

    def _meta(self, q: FinancialQuery) -> dict:
        return {
            "intent": q.intent.value,
            "metric": q.metric.value,
            "aggregation": q.aggregation.value,
            "filters": {k: v for k, v in q.filters.model_dump().items() if v is not None},
            "date_range": q.date_range.model_dump(mode="json", exclude_none=True),
            "group_by": [g.value for g in q.group_by],
            "limit": q.limit,
        }

    def _count_matched(self, q: FinancialQuery) -> int:
        """COUNT(*) over the same filters/date-range, pre-grouping/pre-limit."""
        import sqlalchemy as sa

        from ..models import Transaction
        from .builder import _apply_filters, _base_query, _date_clause

        stmt = _base_query(q, [sa.func.count(sa.distinct(Transaction.transaction_id))])
        stmt = stmt.where(*_date_clause(q))
        stmt = _apply_filters(stmt, q)
        row = self.db.execute(stmt).one()
        return int(row[0])

    # ----- comparison ---------------------------------------------------------

    def execute_comparison(self, q: FinancialQuery, base_result: QueryResult,
                           prev_start: dt.date, prev_end: dt.date) -> QueryResult:
        """Execute the same query against the previous period. The base query
        is already validated, so a deepcopy with new dates is safe."""
        import copy

        # A full-calendar-month base compares against the previous CALENDAR
        # month, not an equal-length day window (Jul 1–31 → Jun 1–30, never
        # May 31 – Jun 30).
        from ..schemas.query import month_bounds

        if (q.date_range.type in (DateRangeType.CALENDAR_MONTH,
                                  DateRangeType.MONTH_BEFORE_PREVIOUS,
                                  DateRangeType.THIS_MONTH)
                and q.date_range.start
                and q.date_range.start.day == 1):
            first_of_month = q.date_range.start
            prev_month_end = first_of_month - dt.timedelta(days=1)
            prev_start = prev_month_end.replace(day=1)
            prev_end = prev_month_end

        new_range = dict(q.date_range.model_dump(mode="json", exclude_none=True))
        new_range["start"] = prev_start.isoformat()
        new_range["end"] = prev_end.isoformat()
        new_range["type"] = DateRangeType.CUSTOM.value
        length_days = (prev_end - prev_start).days + 1
        if length_days >= 28 and (prev_start.day == 1 and prev_end.day >= 28):
            new_range["label"] = prev_start.strftime("%b %Y")
        else:
            new_range["label"] = f"{prev_start.strftime('%b %-d')} – {prev_end.strftime('%b %-d, %Y')}"

        q2 = copy.deepcopy(q)
        q2 = q2.model_copy(update={"date_range": DateRange.model_validate(new_range)})
        result = self.execute(q2)
        result.query_metadata["is_comparison_of"] = True
        return result
