"""Normalize messy LLM JSON into a FinancialQuery-shaped dict.

Small local models often emit near-correct structures with wrong enum names
or misplaced date fields. This layer is deterministic and allowlisted — it
never invents SQL or numbers.
"""
from __future__ import annotations

from typing import Any

from ..schemas.query import (
    Aggregation,
    DateRangeType,
    GroupByDimension,
    Intent,
    Metric,
    SortDirection,
)

_METRIC_ALIASES = {
    "amount": Metric.TRANSACTION_AMOUNT.value,
    "transaction_amount": Metric.TRANSACTION_AMOUNT.value,
    "txn_amount": Metric.TRANSACTION_AMOUNT.value,
    "spend": Metric.TRANSACTION_AMOUNT.value,
    "expense": Metric.TRANSACTION_AMOUNT.value,
    "expenses": Metric.TRANSACTION_AMOUNT.value,
    "count": Metric.TRANSACTION_COUNT.value,
    "transaction_count": Metric.TRANSACTION_COUNT.value,
    "txn_count": Metric.TRANSACTION_COUNT.value,
    "balance": Metric.BALANCE.value,
    "available_balance": Metric.BALANCE.value,
}

_INTENT_ALIASES = {
    "spend_summary": Intent.TRANSACTION_SUMMARY.value,
    "expense_summary": Intent.TRANSACTION_SUMMARY.value,
    "vendor_spend": Intent.TRANSACTION_SUMMARY.value,
    "vendor_expenses": Intent.TRANSACTION_SUMMARY.value,
    "payout_summary": Intent.TRANSACTION_SUMMARY.value,
    "summary": Intent.TRANSACTION_SUMMARY.value,
    "list": Intent.TRANSACTION_LIST.value,
    "transactions": Intent.TRANSACTION_LIST.value,
}

_FILTER_KEYS = {
    "bank_code",
    "bank_name",
    "account_id",
    "transaction_type",
    "description_contains",
    "reference_id",
    "utr_number",
    "min_amount",
    "max_amount",
}

_DATE_KEYS = {
    "type",
    "month",
    "year",
    "n_months",
    "n_days",
    "start",
    "end",
    "label",
}

_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
}


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _coerce_month(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    if isinstance(value, (int, float)) and 1 <= int(value) <= 12:
        import calendar
        return calendar.month_name[int(value)].lower()
    return None


def normalize_llm_query(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return a cleaned copy suitable for FinancialQuery.model_validate."""
    if not isinstance(raw, dict):
        return {}

    out: dict[str, Any] = dict(raw)

    # --- intent ---------------------------------------------------------------
    intent = str(out.get("intent") or "").strip().lower()
    intent = _INTENT_ALIASES.get(intent, intent)
    if intent in {i.value for i in Intent}:
        out["intent"] = intent

    # --- metric ---------------------------------------------------------------
    metric = str(out.get("metric") or "").strip().lower()
    metric = _METRIC_ALIASES.get(metric, metric)
    if metric in {m.value for m in Metric}:
        out["metric"] = metric
    elif intent in (
        Intent.ACCOUNT_BALANCE.value,
        Intent.BANK_BALANCE.value,
        Intent.ACCOUNT_LIST.value,
    ):
        out["metric"] = Metric.BALANCE.value
    elif intent == Intent.BANK_ACCOUNT_COUNT.value:
        out["metric"] = Metric.TRANSACTION_COUNT.value
    else:
        out["metric"] = Metric.TRANSACTION_AMOUNT.value

    # --- aggregation ----------------------------------------------------------
    agg = str(out.get("aggregation") or "").strip().lower()
    if agg not in {a.value for a in Aggregation}:
        if intent in (
            Intent.TRANSACTION_LIST.value,
            Intent.REFERENCE_LOOKUP.value,
            Intent.ACCOUNT_LIST.value,
        ):
            agg = Aggregation.NONE.value
        elif out["metric"] == Metric.TRANSACTION_COUNT.value:
            agg = Aggregation.COUNT.value
        else:
            agg = Aggregation.SUM.value
    out["aggregation"] = agg

    # List intents cannot carry sum/count aggregations from confused models.
    if intent == Intent.TRANSACTION_LIST.value and agg != Aggregation.NONE.value:
        out["intent"] = Intent.TRANSACTION_SUMMARY.value
        intent = Intent.TRANSACTION_SUMMARY.value
    if intent == Intent.TRANSACTION_SUMMARY.value and agg == Aggregation.NONE.value:
        out["aggregation"] = Aggregation.SUM.value
        agg = Aggregation.SUM.value

    # --- filters + lift misplaced date fields ---------------------------------
    filters = dict(_as_dict(out.get("filters")))
    date_range = dict(_as_dict(out.get("date_range")))

    # Common bug: date_range / month stuffed inside filters.
    for key in list(filters.keys()):
        if key in _DATE_KEYS or key == "date_range":
            val = filters.pop(key)
            if key == "date_range":
                if isinstance(val, dict):
                    date_range.update(val)
                elif isinstance(val, str) and val:
                    date_range.setdefault("type", val)
            elif key == "type" and isinstance(val, str):
                date_range.setdefault("type", val)
            elif key in ("month", "year", "n_months", "n_days", "start", "end", "label"):
                date_range.setdefault(key, val)

    # Also accept top-level month/year without date_range object.
    for key in ("month", "year", "n_months", "n_days"):
        if key in out and key not in date_range:
            date_range[key] = out.pop(key)

    # Two months in one field → named-month comparison.
    month_val = date_range.get("month")
    if isinstance(month_val, (list, tuple)) and len(month_val) >= 2:
        m1 = _coerce_month(month_val[0])
        m2 = _coerce_month(month_val[1])
        if m1 and m2:
            date_range["month"] = m1
            out["intent"] = Intent.COMPARISON.value
            intent = Intent.COMPARISON.value
            out["comparison"] = {"against": "named_month", "month": m2}
    else:
        coerced = _coerce_month(month_val)
        if coerced:
            date_range["month"] = coerced
        elif month_val is not None:
            date_range.pop("month", None)

    if not date_range.get("type"):
        if date_range.get("month"):
            date_range["type"] = DateRangeType.CALENDAR_MONTH.value
        elif date_range.get("start") and date_range.get("end"):
            date_range["type"] = DateRangeType.CUSTOM.value
        elif date_range.get("n_days"):
            date_range["type"] = DateRangeType.LAST_N_DAYS.value
        elif date_range.get("n_months"):
            date_range["type"] = DateRangeType.LAST_N_MONTHS.value
        else:
            date_range["type"] = DateRangeType.CALENDAR_MONTH.value

    # Drop placeholder counterparty filters that are not real description text.
    desc = filters.get("description_contains")
    if isinstance(desc, str) and desc.strip().upper() in {
        "VENDOR", "VENDORS", "SUPPLIER", "SUPPLIERS", "PAYOUT", "PAYOUTS",
    }:
        filters.pop("description_contains", None)

    # Keep only allowlisted filter keys.
    filters = {k: v for k, v in filters.items() if k in _FILTER_KEYS and v is not None}
    out["filters"] = filters
    out["date_range"] = date_range

    # --- comparison -----------------------------------------------------------
    cmp = out.get("comparison")
    if isinstance(cmp, str):
        c = cmp.strip().lower()
        if c in ("previous_period", "previous_month", "previous_year"):
            out["comparison"] = {"against": c}
        elif c in _MONTH_NAMES:
            out["comparison"] = {"against": "named_month", "month": c}
            out["intent"] = Intent.COMPARISON.value
            intent = Intent.COMPARISON.value
        else:
            out.pop("comparison", None)
    elif isinstance(cmp, dict):
        raw_cmp = dict(cmp)
        against = str(raw_cmp.get("against") or "").strip().lower()
        year = raw_cmp.get("year")
        if isinstance(year, str) and year.isdigit():
            year = int(year)
        elif not isinstance(year, int):
            year = None

        cleaned: dict[str, Any] | None
        if against in _MONTH_NAMES:
            cleaned = {"against": "named_month", "month": against}
            out["intent"] = Intent.COMPARISON.value
            intent = Intent.COMPARISON.value
        elif against == "named_month":
            month = _coerce_month(raw_cmp.get("month"))
            cleaned = (
                {"against": "named_month", "month": month} if month else None
            )
        elif against in ("previous_period", "previous_month", "previous_year"):
            cleaned = {"against": against}
        else:
            month = _coerce_month(raw_cmp.get("month") or raw_cmp.get("compare_month"))
            if month:
                cleaned = {"against": "named_month", "month": month}
                out["intent"] = Intent.COMPARISON.value
                intent = Intent.COMPARISON.value
            else:
                cleaned = None

        if cleaned and cleaned.get("against") == "named_month" and year is not None:
            cleaned["year"] = year

        if cleaned is None:
            out.pop("comparison", None)
        else:
            out["comparison"] = cleaned
    elif cmp is not None:
        out.pop("comparison", None)

    if intent == Intent.COMPARISON.value and not out.get("comparison"):
        out["comparison"] = {"against": "previous_period"}

    # --- group_by / sort / limit ----------------------------------------------
    group_by = out.get("group_by") or []
    if not isinstance(group_by, list):
        group_by = [group_by]
    allowed_dims = {g.value for g in GroupByDimension}
    out["group_by"] = [str(g) for g in group_by if str(g) in allowed_dims]

    sort = str(out.get("sort") or SortDirection.DESC.value).lower()
    if sort not in {s.value for s in SortDirection}:
        sort = SortDirection.DESC.value
    out["sort"] = sort

    if "limit" in out and out["limit"] is not None:
        try:
            lim = int(out["limit"])
            out["limit"] = max(1, min(lim, 100))
        except (TypeError, ValueError):
            out.pop("limit", None)

    # Balance intents must use balance metric.
    if intent in (Intent.ACCOUNT_BALANCE.value, Intent.BANK_BALANCE.value):
        out["metric"] = Metric.BALANCE.value
        if out["aggregation"] == Aggregation.NONE.value:
            out["aggregation"] = Aggregation.SUM.value

    # Strip unknown top-level keys that break extra=forbid.
    allowed_top = {
        "intent", "metric", "aggregation", "filters", "date_range",
        "comparison", "group_by", "sort", "limit",
    }
    return {k: v for k, v in out.items() if k in allowed_top}
