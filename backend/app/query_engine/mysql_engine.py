"""MySQL query engine — FinancialQuery → compiled SQL → QueryResult.

Every number comes from MySQL; the LLM never generates SQL. Sensitive fields
(account_number, utr_number) are masked at the engine boundary.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from copy import deepcopy
from typing import Any

import pymysql
from pymysql.connections import Connection

from ..schemas.query import (
    Aggregation,
    DateRange,
    DateRangeType,
    FinancialQuery,
    Intent,
)
from .cache import get_cached_result, put_cached_result
from .mysql_builder import compile_count, compile_query
from .mysql_store import connect as mysql_connect
from .mysql_url import mask_mysql_url, normalize_mysql_url
from .result import QueryResult


def _cell(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "quantize"):  # Decimal
        return float(v)
    return v


def _rows_to_dicts(con: Connection, compiled) -> list[dict]:
    with con.cursor() as cur:
        cur.execute(compiled.sql, compiled.params)
        rows = cur.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [{k: _cell(v) for k, v in row.items()} for row in rows]
    raise TypeError("expected DictCursor rows from MySQL connection")



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


def _url_fingerprint(url: str) -> str:
    return hashlib.sha256(normalize_mysql_url(url).encode("utf-8")).hexdigest()[:16]


class MySQLQueryEngine:
    """Read-only MySQL executor with optional Redis/memory result cache."""

    def __init__(
        self,
        con: Connection,
        *,
        database_url: str,
        owns_connection: bool = True,
    ):
        self._con = con
        self._owns = owns_connection
        self.database_url = database_url
        self.database_url_masked = mask_mysql_url(database_url)
        self._cache_scope = _url_fingerprint(database_url)
        self._configure_session()

    def _configure_session(self) -> None:
        with self._con.cursor() as cur:
            try:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            except pymysql.Error:
                pass
            try:
                cur.execute("SET SESSION MAX_EXECUTION_TIME=10000")
            except pymysql.Error:
                pass

    @classmethod
    def from_url(cls, database_url: str) -> "MySQLQueryEngine":
        url = normalize_mysql_url(database_url)
        con = mysql_connect(url, autocommit=True)
        return cls(con, database_url=url, owns_connection=True)

    @classmethod
    def from_connection(
        cls, con: Connection, *, database_url: str
    ) -> "MySQLQueryEngine":
        return cls(con, database_url=database_url, owns_connection=False)

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
        cached = get_cached_result(q, scope=self._cache_scope)
        if cached is not None:
            return cached

        result = self._execute_uncached(q)
        result.query_metadata["cache_hit"] = False
        put_cached_result(q, result, scope=self._cache_scope)
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
            "backend": "mysql",
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
        if q.intent == Intent.BANK_COUNT:
            return self._execute_bank_count(q)
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

    def _execute_bank_count(self, q: FinancialQuery) -> QueryResult:
        compiled = compile_query(q)
        rows = _rows_to_dicts(self._con, compiled)
        return QueryResult(
            summary={
                "value": (rows[0]["value"] if rows else 0) or 0,
                "record_count": (rows[0]["value"] if rows else 0) or 0,
            },
            query_metadata=self._meta(q, compiled.sql),
        )

    def _count_matched(self, q: FinancialQuery) -> int:
        compiled = compile_count(q)
        with self._con.cursor() as cur:
            cur.execute(compiled.sql, compiled.params)
            row = cur.fetchone()
        if not row:
            return 0
        if isinstance(row, dict):
            return int(next(iter(row.values())) or 0)
        return int(row[0] or 0)

    def execute_comparison(
        self,
        q: FinancialQuery,
        base_result: QueryResult,
        previous_range_start: dt.date,
        previous_range_end: dt.date,
    ) -> QueryResult:
        prev_start, prev_end = previous_range_start, previous_range_end
        length_days = (prev_end - prev_start).days + 1
        if length_days >= 28 and prev_start.day == 1 and prev_end.day >= 28:
            label = prev_start.strftime("%b %Y")
        else:
            label = f"{prev_start.isoformat()} to {prev_end.isoformat()}"
        q2 = deepcopy(q)
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
            "transaction": "`transaction`",
            "banks": "bank",
            "accounts": "account",
            "transactions": "`transaction`",
        }
        if table not in allowed:
            raise ValueError(f"table '{table}' not allowlisted")
        quoted = allowed[table]
        with self._con.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM {quoted}")
            row = cur.fetchone()
        if isinstance(row, dict):
            return int(row.get("cnt") or 0)
        return int(row[0] if row else 0)

    def ping(self) -> bool:
        try:
            self._con.ping(reconnect=True)
            return True
        except pymysql.Error:
            return False
