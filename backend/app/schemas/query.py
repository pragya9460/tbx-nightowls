"""Structured financial query representation.

This is the semantic layer between the LLM and PostgreSQL. The LLM emits JSON
that must validate against these models *before* anything touches the
database. Every enum is a closed allowlist — unsupported values are rejected
with a structured error, which is the foundation of hallucination prevention.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Intent(str, Enum):
    VENDOR_PAYOUT_SUMMARY = "vendor_payout_summary"   # metric sum/count over payouts
    UNRECONCILED_LIST = "unreconciled_list"           # list unreconciled transactions
    VENDOR_SPEND = "vendor_spend"                     # payout spend for one vendor
    TOP_VENDORS = "top_vendors"                       # group payouts by vendor, sum, top N
    TRANSACTION_COUNT = "transaction_count"           # count transactions matching filters
    COMPARISON = "comparison"                         # same metric, two date ranges


class Metric(str, Enum):
    PAYOUT_AMOUNT = "payout_amount"
    PAYOUT_COUNT = "payout_count"
    TRANSACTION_AMOUNT = "transaction_amount"
    TRANSACTION_COUNT = "transaction_count"


class Aggregation(str, Enum):
    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MAX = "max"
    MIN = "min"
    NONE = "none"


class GroupByDimension(str, Enum):
    VENDOR = "vendor"
    VENDOR_CATEGORY = "vendor_category"
    TRANSACTION_CATEGORY = "category"
    ACCOUNT = "account"
    PAYOUT_STATUS = "payout_status"
    RECONCILIATION_STATUS = "reconciliation_status"
    MONTH = "month"


class DateRangeType(str, Enum):
    CALENDAR_MONTH = "calendar_month"   # explicit resolved month
    LAST_N_MONTHS = "last_n_months"
    CUSTOM = "custom"                   # explicit start/end dates
    ALL_TIME = "all_time"
    MONTH_BEFORE_PREVIOUS = "month_before_previous"


class DateRange(BaseModel):
    """A resolved date range — always concrete dates by execution time.

    Relative expressions ("last month", "previous quarter") are resolved to
    absolute dates by the backend, never trusted from the LLM.
    """

    type: DateRangeType
    start: dt.date | None = None
    end: dt.date | None = None
    n_months: int | None = Field(default=None, ge=1, le=24)
    label: str | None = None  # human label, e.g. "Aug 2026"

    @model_validator(mode="after")
    def _check_consistency(self) -> "DateRange":
        if self.type == DateRangeType.ALL_TIME:
            return self
        if self.type == DateRangeType.LAST_N_MONTHS:
            if self.n_months is None:
                raise ValueError("n_months is required for last_n_months ranges")
            return self
        if self.start is None or self.end is None:
            raise ValueError("start and end are required unless range is all_time/last_n_months")
        if self.start > self.end:
            raise ValueError("start date must be <= end date")
        return self


class ComparisonSpec(BaseModel):
    """Second date range for comparison intents."""

    against: Literal["previous_period", "previous_month", "previous_year"] = (
        "previous_period"
    )


class QueryFilters(BaseModel):
    """Explicit allowlist of filterable dimensions."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: str | None = None
    vendor_name: str | None = None
    payout_status: str | None = None
    reconciliation_status: str | None = None
    transaction_category: str | None = None
    vendor_category: str | None = None
    account: str | None = None
    transaction_type: str | None = None

    @field_validator("payout_status")
    @classmethod
    def _payout_status_valid(cls, v: str | None) -> str | None:
        allowed = {"paid", "pending", "failed"}
        if v is not None and v not in allowed:
            raise ValueError(f"payout_status must be one of {sorted(allowed)}")
        return v

    @field_validator("reconciliation_status")
    @classmethod
    def _rec_status_valid(cls, v: str | None) -> str | None:
        allowed = {"reconciled", "unreconciled", "pending"}
        if v is not None and v not in allowed:
            raise ValueError(f"reconciliation_status must be one of {sorted(allowed)}")
        return v

    @field_validator("transaction_type")
    @classmethod
    def _txn_type_valid(cls, v: str | None) -> str | None:
        allowed = {"debit", "credit"}
        if v is not None and v not in allowed:
            raise ValueError(f"transaction_type must be one of {sorted(allowed)}")
        return v


class FinancialQuery(BaseModel):
    """The one and only query shape the engine accepts."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    metric: Metric
    aggregation: Aggregation
    filters: QueryFilters = Field(default_factory=QueryFilters)
    date_range: DateRange
    comparison: ComparisonSpec | None = None
    group_by: list[GroupByDimension] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _check_intent_metric(self) -> "FinancialQuery":
        payout_intents = {Intent.VENDOR_PAYOUT_SUMMARY, Intent.VENDOR_SPEND, Intent.TOP_VENDORS, Intent.COMPARISON}
        payout_metrics = {Metric.PAYOUT_AMOUNT, Metric.PAYOUT_COUNT}
        txn_metrics = {Metric.TRANSACTION_AMOUNT, Metric.TRANSACTION_COUNT}

        if self.intent in payout_intents and self.metric not in payout_metrics:
            raise ValueError(
                f"intent '{self.intent.value}' supports payout metrics {sorted(m.value for m in payout_metrics)}"
            )
        if self.intent == Intent.UNRECONCILED_LIST and self.metric not in txn_metrics:
            raise ValueError(
                "unreconciled_list supports transaction_amount/transaction_count metrics"
            )
        if self.intent == Intent.TOP_VENDORS and GroupByDimension.VENDOR not in self.group_by:
            raise ValueError("top_vendors requires group_by=['vendor']")
        if self.intent == Intent.COMPARISON and self.comparison is None:
            raise ValueError("comparison intent requires a comparison spec")
        if self.intent != Intent.COMPARISON and self.comparison is not None:
            raise ValueError("comparison spec only allowed with comparison intent")
        if self.intent == Intent.UNRECONCILED_LIST and self.limit is None:
            self.limit = 50  # safe default for list questions
        return self


class SupportedField(str, Enum):
    """Public registry of what the semantic layer supports — surfaced to the
    user when a question can't be answered."""

    INTENTS = "intents"
    METRICS = "metrics"
    FILTERS = "filters"
    GROUP_BY = "group_by"
    AGGREGATIONS = "aggregations"
    DATE_RANGES = "date_ranges"


def supported_capabilities() -> dict[str, list[str]]:
    """Single source of truth for what the assistant can answer. Used by the
    validator to reject unsupported fields and by the UI/help text."""
    return {
        "intents": [i.value for i in Intent],
        "metrics": [m.value for m in Metric],
        "filters": list(QueryFilters.model_fields.keys()),
        "group_by": [g.value for g in GroupByDimension],
        "aggregations": [a.value for a in Aggregation],
        "date_ranges": [d.value for d in DateRangeType],
    }


# ---------------------------------------------------------------------------
# Error taxonomy — explicit distinction between supported / unsupported /
# ambiguous / invalid, per spec §12.
# ---------------------------------------------------------------------------

class QueryRefusalReason(str, Enum):
    UNSUPPORTED_METRIC = "unsupported_metric"
    UNSUPPORTED_FIELD = "unsupported_field"
    AMBIGUOUS = "ambiguous"
    INVALID_STRUCTURE = "invalid_structure"
    NO_DATA = "no_data"


class QueryRefusal(BaseModel):
    """Returned instead of executing when a question cannot be answered."""

    reason: QueryRefusalReason
    message: str
    suggestions: list[str] = Field(default_factory=list)
    supported: dict[str, list[str]] | None = None


def refusal(
    reason: QueryRefusalReason,
    message: str,
    suggestions: list[str] | None = None,
    include_supported: bool = False,
) -> QueryRefusal:
    return QueryRefusal(
        reason=reason,
        message=message,
        suggestions=suggestions or [],
        supported=supported_capabilities() if include_supported else None,
    )


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    """First and last calendar day of a month."""
    start = dt.date(year, month, 1)
    if month == 12:
        end = dt.date(year, 12, 31)
    else:
        end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return start, end


def resolve_date_range(today: dt.date, spec: dict) -> DateRange:
    """Resolve a relative range descriptor to absolute dates.

    Deterministic: takes `today` explicitly rather than reading the clock,
    so tests and evaluation runs are reproducible.
    """
    range_type = spec.get("type")
    if range_type == DateRangeType.CALENDAR_MONTH.value:
        if spec.get("start") and spec.get("end"):
            start = dt.date.fromisoformat(spec["start"])
            end = dt.date.fromisoformat(spec["end"])
        else:
            # "last month" relative to today's month
            first_of_current = today.replace(day=1)
            start = first_of_current - dt.timedelta(days=1)
            start = start.replace(day=1)
            end = month_bounds(start.year, start.month)[1]
        return DateRange(
            type=DateRangeType.CALENDAR_MONTH, start=start, end=end,
            label=start.strftime("%b %Y"),
        )
    if range_type == DateRangeType.LAST_N_MONTHS.value:
        n = int(spec.get("n_months") or 1)
        anchor_end = today.replace(day=1) - dt.timedelta(days=1)  # end = last completed month
        months: list[dt.date] = []
        cursor = anchor_end
        for _ in range(n):
            months.append(dt.date(cursor.year, cursor.month, 1))
            cursor = (dt.date(cursor.year, cursor.month, 1) - dt.timedelta(days=1))
        start = min(months)
        return DateRange(
            type=DateRangeType.LAST_N_MONTHS, start=start, end=anchor_end, n_months=n,
            label=f"{n} month{'s' if n != 1 else ''} to {anchor_end.strftime('%b %Y')}",
        )
    if range_type == DateRangeType.MONTH_BEFORE_PREVIOUS.value:
        first_of_current = today.replace(day=1)
        last_month = first_of_current - dt.timedelta(days=1)
        first_of_last_month = last_month.replace(day=1)
        month_before = first_of_last_month - dt.timedelta(days=1)
        start = month_before.replace(day=1)
        end = month_bounds(month_before.year, month_before.month)[1]
        return DateRange(
            type=DateRangeType.MONTH_BEFORE_PREVIOUS, start=start, end=end,
            label=start.strftime("%b %Y"),
        )
    if range_type == DateRangeType.CUSTOM.value:
        start = dt.date.fromisoformat(spec["start"])
        end = dt.date.fromisoformat(spec["end"])
        return DateRange(type=DateRangeType.CUSTOM, start=start, end=end,
                         label=f"{start.isoformat()} to {end.isoformat()}")
    if range_type == DateRangeType.ALL_TIME.value:
        return DateRange(type=DateRangeType.ALL_TIME, label="all time")
    raise ValueError(f"unknown date range type: {range_type}")


def previous_period(dr: DateRange) -> DateRange:
    """The immediately preceding period of equal length (for comparisons)."""
    if dr.start is None or dr.end is None:
        raise ValueError("cannot compute previous period of an all_time range")
    length = (dr.end - dr.start).days + 1
    end = dr.start - dt.timedelta(days=1)
    start = end - dt.timedelta(days=length - 1)
    return DateRange(
        type=DateRangeType.CUSTOM, start=start, end=end,
        label=f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}" if hasattr(start, 'strftime') else None,
    )


def previous_month(today: dt.date) -> DateRange:
    return resolve_date_range(today, {"type": "calendar_month"})


def month_before_previous_range(today: dt.date) -> DateRange:
    return resolve_date_range(today, {"type": "month_before_previous"})
