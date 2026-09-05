"""Deterministic Text-to-SQL: FinancialQuery → MySQL SELECT.

The LLM never produces this SQL. Only a validated ``FinancialQuery`` enters
this compiler. Output is a single parameterized SELECT over the TBX schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

import sqlglot
from sqlglot import exp

from ..schemas.query import (
    Aggregation,
    DateRangeType,
    FinancialQuery,
    GroupByDimension,
    Intent,
    Metric,
    SortDirection,
)


@dataclass
class CompiledQuery:
    """A safe, parameterized MySQL SELECT (PyMySQL %(name)s style)."""

    sql: str
    params: dict[str, object] = field(default_factory=dict)


_TXN_FROM = (
    "`transaction` t "
    "JOIN account a ON t.account_id = a.account_id "
    "JOIN bank b ON a.bank_code = b.bank_code"
)
_ACCT_FROM = "account a JOIN bank b ON a.bank_code = b.bank_code"

_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _p(name: str) -> str:
    return f"%({name})s"


def _to_pymysql_params(sql: str) -> str:
    return _PARAM.sub(lambda m: f"%({m.group(1)})s", sql)


def _metric_expr(q: FinancialQuery) -> str:
    if q.metric == Metric.BALANCE:
        raise ValueError("balance metric uses dedicated balance queries")
    if q.aggregation == Aggregation.COUNT:
        return "COUNT(DISTINCT t.transaction_id) AS value"
    if q.aggregation == Aggregation.NONE:
        return "1 AS _one"
    fn = {
        Aggregation.SUM: "SUM",
        Aggregation.AVG: "AVG",
        Aggregation.MAX: "MAX",
        Aggregation.MIN: "MIN",
    }.get(q.aggregation)
    if fn is None:
        raise ValueError(
            f"aggregation '{q.aggregation.value}' not supported for metric '{q.metric.value}'"
        )
    return f"{fn}(t.transaction_amount) AS value"


def _group_selects(q: FinancialQuery) -> list[str]:
    out: list[str] = []
    for dim in q.group_by:
        if dim == GroupByDimension.BANK:
            out.append("b.bank_code AS bank_code")
            out.append("b.bank_name AS bank_name")
        elif dim == GroupByDimension.ACCOUNT:
            out.append("a.account_id AS account_id")
            out.append("a.account_number AS account_number_masked")
        elif dim == GroupByDimension.TRANSACTION_TYPE:
            out.append("t.transaction_type AS transaction_type")
        elif dim == GroupByDimension.MONTH:
            out.append(
                "CONCAT(YEAR(t.transaction_date), '-', "
                "LPAD(MONTH(t.transaction_date), 2, '0')) AS month"
            )
    return out


def _group_by_clauses(q: FinancialQuery) -> list[str]:
    exprs: list[str] = []
    for dim in q.group_by:
        if dim == GroupByDimension.BANK:
            exprs.extend(["b.bank_code", "b.bank_name"])
        elif dim == GroupByDimension.ACCOUNT:
            exprs.extend(["a.account_id", "a.account_number"])
        elif dim == GroupByDimension.TRANSACTION_TYPE:
            exprs.append("t.transaction_type")
        elif dim == GroupByDimension.MONTH:
            exprs.append(
                "CONCAT(YEAR(t.transaction_date), '-', "
                "LPAD(MONTH(t.transaction_date), 2, '0'))"
            )
    return exprs


def _apply_txn_filters(q: FinancialQuery, params: dict[str, object]) -> list[str]:
    f = q.filters
    clauses: list[str] = []
    if f.transaction_type:
        clauses.append(f"t.transaction_type = {_p('transaction_type')}")
        params["transaction_type"] = f.transaction_type
    if f.description_contains:
        clauses.append(
            f"LOWER(t.description) LIKE LOWER({_p('description_contains')})"
        )
        params["description_contains"] = f"%{f.description_contains}%"
    if f.reference_id:
        clauses.append(f"t.transaction_reference_id = {_p('reference_id')}")
        params["reference_id"] = f.reference_id
    if f.utr_number:
        clauses.append(f"t.utr_number = {_p('utr_number')}")
        params["utr_number"] = f.utr_number
    if f.min_amount is not None:
        clauses.append(f"t.transaction_amount >= {_p('min_amount')}")
        params["min_amount"] = f.min_amount
    if f.max_amount is not None:
        clauses.append(f"t.transaction_amount <= {_p('max_amount')}")
        params["max_amount"] = f.max_amount
    if f.bank_code:
        clauses.append(f"b.bank_code = {_p('bank_code')}")
        params["bank_code"] = f.bank_code
    elif f.bank_name:
        clauses.append(f"LOWER(b.bank_name) LIKE LOWER({_p('bank_name')})")
        params["bank_name"] = f"%{f.bank_name}%"
    if f.account_id:
        clauses.append(f"t.account_id = {_p('account_id')}")
        params["account_id"] = f.account_id
    return clauses


def _bank_filters(q: FinancialQuery, params: dict[str, object], alias: str = "b") -> list[str]:
    f = q.filters
    clauses: list[str] = []
    if f.bank_code:
        clauses.append(f"{alias}.bank_code = {_p('bank_code')}")
        params["bank_code"] = f.bank_code
    elif f.bank_name:
        clauses.append(f"LOWER({alias}.bank_name) LIKE LOWER({_p('bank_name')})")
        params["bank_name"] = f"%{f.bank_name}%"
    return clauses


def _date_filters(q: FinancialQuery, params: dict[str, object]) -> list[str]:
    if q.date_range.type == DateRangeType.ALL_TIME or q.date_range.start is None:
        return []
    params["date_start"] = q.date_range.start.isoformat()
    params["date_end"] = q.date_range.end.isoformat()
    return [
        f"DATE(t.transaction_date) >= {_p('date_start')}",
        f"DATE(t.transaction_date) <= {_p('date_end')}",
    ]


def _assert_safe_select(sql: str) -> None:
    # Normalize MySQL param markers and DATE_FORMAT escapes for sqlglot.
    parse_sql = sql.replace("%%", "%")
    parse_sql = re.sub(r"%\([A-Za-z_][A-Za-z0-9_]*\)s", "?", parse_sql)
    parsed = sqlglot.parse(parse_sql, read="mysql")
    if len(parsed) != 1:
        raise ValueError("compiled SQL must be a single statement")
    tree = parsed[0]
    if not isinstance(tree, exp.Select):
        raise ValueError(f"compiled SQL must be SELECT, got {type(tree).__name__}")
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Create,
        exp.Command,
        exp.Copy,
        exp.Alter,
    )
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise ValueError(f"forbidden SQL node in compiled query: {type(node).__name__}")


def _finalize(sql: str, params: dict[str, object]) -> CompiledQuery:
    sql = "\n".join(line for line in sql.splitlines() if line.strip())
    _assert_safe_select(sql)
    return CompiledQuery(sql=sql, params=params)


def _order_value(q: FinancialQuery) -> str:
    return "DESC" if q.sort == SortDirection.DESC else "ASC"


def _build_list(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    where = _apply_txn_filters(q, params) + _date_filters(q, params)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit = q.limit or 20
    params["limit_n"] = limit
    if q.filters.min_amount is not None and q.filters.max_amount is None:
        order = "t.transaction_amount DESC"
    else:
        order = "t.transaction_date DESC"
    sql = f"""
SELECT
  t.transaction_id,
  t.transaction_date,
  t.transaction_type,
  t.description,
  t.transaction_amount,
  t.transaction_reference_id,
  t.utr_number,
  a.account_number,
  b.bank_code,
  b.bank_name
FROM {_TXN_FROM}
{where_sql}
ORDER BY {order}
LIMIT {_p('limit_n')}
""".strip()
    return _finalize(sql, params)


def _build_agg(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    selects = [_metric_expr(q)] + _group_selects(q)
    where = _apply_txn_filters(q, params) + _date_filters(q, params)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    group_aliases = _group_by_clauses(q)
    group_sql = f"GROUP BY {', '.join(group_aliases)}" if group_aliases else ""
    limit_sql = ""
    if q.limit:
        params["limit_n"] = q.limit
        limit_sql = f"LIMIT {_p('limit_n')}"
    sql = f"""
SELECT {', '.join(selects)}
FROM {_TXN_FROM}
{where_sql}
{group_sql}
ORDER BY value {_order_value(q)}
{limit_sql}
""".strip()
    return _finalize(sql, params)


def _build_monthly_trend(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    if q.aggregation == Aggregation.COUNT:
        metric = "COUNT(DISTINCT t.transaction_id) AS value"
    else:
        metric = _metric_expr(q)
    where = _apply_txn_filters(q, params) + _date_filters(q, params)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
SELECT
  YEAR(t.transaction_date) AS txn_year,
  MONTH(t.transaction_date) AS txn_month,
  {metric}
FROM {_TXN_FROM}
{where_sql}
GROUP BY YEAR(t.transaction_date), MONTH(t.transaction_date)
ORDER BY txn_year ASC, txn_month ASC
""".strip()
    return _finalize(sql, params)


def _build_balance(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    f = q.filters
    if f.account_id:
        params["account_id"] = f.account_id
        sql = f"""
SELECT
  a.account_id,
  a.account_number,
  a.available_balance,
  a.program_id,
  b.bank_code,
  b.bank_name
FROM {_ACCT_FROM}
WHERE a.account_id = {_p('account_id')}
""".strip()
        return _finalize(sql, params)

    bank_where = _bank_filters(q, params)
    where_sql = f"WHERE {' AND '.join(bank_where)}" if bank_where else ""

    if GroupByDimension.BANK in q.group_by:
        params["limit_n"] = q.limit or 10
        sql = f"""
SELECT
  b.bank_code,
  b.bank_name,
  SUM(a.available_balance) AS value,
  COUNT(DISTINCT a.account_id) AS account_count
FROM {_ACCT_FROM}
{where_sql}
GROUP BY b.bank_code, b.bank_name
ORDER BY value {_order_value(q)}
LIMIT {_p('limit_n')}
""".strip()
        return _finalize(sql, params)

    if GroupByDimension.ACCOUNT in q.group_by:
        params["limit_n"] = q.limit or 10
        sql = f"""
SELECT
  a.account_id,
  a.account_number,
  a.available_balance AS value,
  a.program_id,
  b.bank_code,
  b.bank_name
FROM {_ACCT_FROM}
{where_sql}
ORDER BY value {_order_value(q)}
LIMIT {_p('limit_n')}
""".strip()
        return _finalize(sql, params)

    sql = f"""
SELECT
  SUM(a.available_balance) AS value,
  COUNT(DISTINCT a.account_id) AS account_count
FROM {_ACCT_FROM}
{where_sql}
""".strip()
    return _finalize(sql, params)


def _build_account_list(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    bank_where = _bank_filters(q, params)
    where_sql = f"WHERE {' AND '.join(bank_where)}" if bank_where else ""
    params["limit_n"] = q.limit or 20
    sql = f"""
SELECT
  a.account_id,
  a.account_number,
  a.available_balance,
  a.program_id,
  b.bank_code,
  b.bank_name
FROM {_ACCT_FROM}
{where_sql}
ORDER BY a.available_balance {_order_value(q)}
LIMIT {_p('limit_n')}
""".strip()
    return _finalize(sql, params)


def _build_account_count(q: FinancialQuery) -> CompiledQuery:
    params: dict[str, object] = {}
    bank_where = _bank_filters(q, params)
    where_sql = f"WHERE {' AND '.join(bank_where)}" if bank_where else ""
    params["limit_n"] = q.limit or 10
    sql = f"""
SELECT
  b.bank_code,
  b.bank_name,
  COUNT(DISTINCT a.account_id) AS value
FROM {_ACCT_FROM}
{where_sql}
GROUP BY b.bank_code, b.bank_name
ORDER BY value {_order_value(q)}
LIMIT {_p('limit_n')}
""".strip()
    return _finalize(sql, params)


def compile_query(q: FinancialQuery) -> CompiledQuery:
    """Compile a validated FinancialQuery into a MySQL SELECT."""
    if q.intent in (Intent.ACCOUNT_BALANCE, Intent.BANK_BALANCE):
        return _build_balance(q)
    if q.intent == Intent.ACCOUNT_LIST:
        return _build_account_list(q)
    if q.intent == Intent.BANK_ACCOUNT_COUNT:
        return _build_account_count(q)
    if q.intent == Intent.MONTHLY_TREND:
        return _build_monthly_trend(q)
    if q.intent == Intent.REFERENCE_LOOKUP or q.aggregation == Aggregation.NONE:
        return _build_list(q)
    return _build_agg(q)


def compile_count(q: FinancialQuery) -> CompiledQuery:
    """COUNT(*) over the same filters/date-range (pre-group / pre-limit)."""
    if q.intent in (
        Intent.ACCOUNT_BALANCE,
        Intent.BANK_BALANCE,
        Intent.ACCOUNT_LIST,
        Intent.BANK_ACCOUNT_COUNT,
    ):
        params: dict[str, object] = {}
        if q.filters.account_id:
            params["account_id"] = q.filters.account_id
            sql = f"""
SELECT COUNT(*) AS cnt
FROM {_ACCT_FROM}
WHERE a.account_id = {_p('account_id')}
""".strip()
            return _finalize(sql, params)
        bank_where = _bank_filters(q, params)
        where_sql = f"WHERE {' AND '.join(bank_where)}" if bank_where else ""
        sql = f"""
SELECT COUNT(DISTINCT a.account_id) AS cnt
FROM {_ACCT_FROM}
{where_sql}
""".strip()
        return _finalize(sql, params)

    params: dict[str, object] = {}
    where = _apply_txn_filters(q, params) + _date_filters(q, params)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
SELECT COUNT(DISTINCT t.transaction_id) AS cnt
FROM {_TXN_FROM}
{where_sql}
""".strip()
    return _finalize(sql, params)
