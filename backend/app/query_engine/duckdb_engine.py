"""DuckDB query engine — FinancialQuery → compiled SQL → QueryResult.

Every number comes from DuckDB; the LLM never sees raw SQL generation.
Sensitive fields (account_number, utr_number) are masked here, at the engine
boundary, so no downstream code can accidentally leak them.
"""
from __future__ import annotations

import datetime as dt
from copy import deepcopy
from pathlib import Path
from typing import Any

import duckdb

from ..schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    Intent,
)
from .cache import get_cached_result, put_cached_result
from .duckdb_builder import compile_count, compile_query
from .duckdb_store import connect_readonly, default_duckdb_path, ensure_duckdb
from .result import QueryResult


def _cell(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "quantize"):  # Decimal
        return float(v)
    return v


def _rows_to_dicts(con: duckdb.DuckDBPyConnection, compiled) -> list[dict]:
    cur = con.execute(compiled.sql, compiled.params)
    cols = [d[0] for d in cur.description]
    return [{cols[i]: _cell(row[i]) for i in range(len(cols))} for row in cur.fetchall()]


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


class DuckDBQueryEngine:
    """Read-only DuckDB executor with optional Redis/memory result cache."""

    def __init__(self, con: duckdb.DuckDBPyConnection, *, owns_connection: bool = True):
        self._con = con
        self._owns = owns_connection

    @classmethod
    def from_path(cls, db_path: Path | str | None = None) -> "DuckDBQueryEngine":
        path = Path(db_path) if db_path else default_duckdb_path()
        ensure_duckdb(db_path=path)
        return cls(connect_readonly(path), owns_connection=True)

    @classmethod
    def from_connection(cls, con: duckdb.DuckDBPyConnection) -> "DuckDBQueryEngine":
        """Wrap an existing connection (e.g. in-memory test DB)."""
        return cls(con, owns_connection=False)

    def close(self) -> None:
        if self._owns and self._con is not None:
            self._con.close()
            self._con = None  # type: ignore[assignment]

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def execute(self, q: FinancialQuery) -> QueryResult:
        cached = get_cached_result(q)
        if cached is not None:
            return cached

        result = self._execute_uncached(q)
        result.query_metadata["cache_hit"] = False
        put_cached_result(q, result)
        return result

    def _meta(self, q: FinancialQuery, sql: str | None = None) -> dict:
        meta = {
            "intent": q.intent.value,
            "metric": q.metric.value,
            "aggregation": q.aggregation.value,
            "filters": {k: v for k, v in q.filters.model_dump().items() if v is not None},
            "date_range": q.date_range.model_dump(mode="json", exclude_none=True),
            "group_by": [g.value for g in q.group_by],
            "limit": q.limit,
            "backend": "duckdb",
        }
        if sql:
            meta["sql"] = sql
        return meta

    def _execute_uncached(self, q: FinancialQuery) -> QueryResult:
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

    def _execute_transaction_query(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = _rows_to_dicts(self._con, compiled)
        meta = self._meta(q, compiled.sql)

        if q.aggregation == Aggregation.NONE:
            return QueryResult(
                summary={"record_count": self._count_matched(q)},
                records=[_mask_record(r) for r in rows],
                query_metadata=meta,
            )

        matched = self._count_matched(q)

        if q.group_by:
            total = sum(r.get("value", 0) or 0 for r in rows)
            if q.limit is not None and len(rows) == q.limit:
                total_q = q.model_copy(deep=True, update={"limit": None})
                total_rows = _rows_to_dicts(self._con, compile_query(total_q))
                total = sum(r.get("value", 0) or 0 for r in total_rows)
            return QueryResult(
                summary={"value": total, "record_count": matched},
                breakdown=[_mask_record(r) for r in rows],
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

    def _execute_reference_lookup(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = _rows_to_dicts(self._con, compiled)
        return QueryResult(
            summary={"record_count": len(rows)},
            records=[_mask_record(r) for r in rows],
            query_metadata=self._meta(q, compiled.sql),
        )

    def _execute_monthly_trend(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = _rows_to_dicts(self._con, compiled)
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
            query_metadata=self._meta(q, compiled.sql),
        )

    def _execute_balance(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = [_mask_record(r) for r in _rows_to_dicts(self._con, compiled)]
        meta = self._meta(q, compiled.sql)

        if q.filters.account_id:
            if not rows:
                return QueryResult(
                    summary={"value": 0, "record_count": 0, "not_found": True},
                    query_metadata=meta,
                )
            r = rows[0]
            return QueryResult(
                summary={"value": r["available_balance"], "record_count": 1},
                breakdown=rows,
                query_metadata=meta,
            )
        if q.group_by:
            top = rows[0] if rows else None
            return QueryResult(
                summary={
                    "value": top["value"] if top else 0,
                    "record_count": sum(r.get("account_count", 1) for r in rows),
                },
                breakdown=rows,
                query_metadata=meta,
            )
        total = rows[0] if rows else {"value": 0, "account_count": 0}
        return QueryResult(
            summary={
                "value": total.get("value") or 0,
                "record_count": total.get("account_count") or 0,
            },
            query_metadata=meta,
        )

    def _execute_account_list(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = [_mask_record(r) for r in _rows_to_dicts(self._con, compiled)]
        return QueryResult(
            summary={
                "record_count": len(rows),
                "value": sum(r["available_balance"] for r in rows),
            },
            records=rows,
            query_metadata=self._meta(q, compiled.sql),
        )

    def _execute_bank_account_count(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = _rows_to_dicts(self._con, compiled)
        return QueryResult(
            summary={
                "value": sum(r["value"] for r in rows),
                "record_count": len(rows),
            },
            breakdown=rows,
            query_metadata=self._meta(q, compiled.sql),
        )

    def _count_matched(self, q: FinancialQuery) -> int:
        compiled = compile_count(q)
        row = self._con.execute(compiled.sql, compiled.params).fetchone()
        return int(row[0] or 0) if row else 0

    def execute_comparison(
        self,
        q: FinancialQuery,
        base_result: QueryResult,
        previous_range_start: dt.date,
        previous_range_end: dt.date,
    ) -> QueryResult:
        """Execute the same query against the previous period."""
        prev_start, prev_end = previous_range_start, previous_range_end
        if (
            q.date_range.type in (
                DateRangeType.CALENDAR_MONTH,
                DateRangeType.MONTH_BEFORE_PREVIOUS,
                DateRangeType.THIS_MONTH,
            )
            and q.date_range.start
            and q.date_range.start.day == 1
        ):
            first_of_month = q.date_range.start
            prev_month_end = first_of_month - dt.timedelta(days=1)
            prev_start = prev_month_end.replace(day=1)
            prev_end = prev_month_end

        q2 = deepcopy(q)
        length_days = (prev_end - prev_start).days + 1
        if length_days >= 28 and prev_start.day == 1 and prev_end.day >= 28:
            label = prev_start.strftime("%b %Y")
        else:
            label = f"{prev_start.strftime('%b %-d')} – {prev_end.strftime('%b %-d, %Y')}"
        q2.date_range = DateRange(
            type=DateRangeType.CUSTOM,
            start=prev_start,
            end=prev_end,
            label=label,
        )
        result = self.execute(q2)
        result.query_metadata["is_comparison_of"] = True
        return result

    def count_total(self, table: str) -> int:
        allowed = {
            "bank": "bank",
            "account": "account",
            "transaction": '"transaction"',
            "banks": "bank",
            "accounts": "account",
            "transactions": '"transaction"',
        }
        if table not in allowed:
            raise ValueError(f"table '{table}' not allowlisted")
        quoted = allowed[table]
        return int(self._con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
