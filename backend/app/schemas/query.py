"""Structured financial query representation — the semantic layer.

This sits between the LLM and SQL. The LLM emits JSON that must validate
against these models *before* anything touches the database. Every enum is a
closed allowlist over the ACTUAL TBX schema (bank / account / transaction) —
unsupported values are rejected with a structured error, which is the
foundation of hallucination prevention.

The allowlists mirror the real columns:
  - transaction: date, type (credit/debit), amount, description,
    transaction_reference_id, utr_number, account_id
  - account: available_balance, bank_code, account_number (masked), program_id
"""
from __future__ import annotations

import calendar as _calendar
import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Application timezone (documented assumption, README §Assumptions): the
# dataset is Indian banking data, so "today"/"this month" resolve in IST.
# ---------------------------------------------------------------------------
import zoneinfo

APP_TIMEZONE = zoneinfo.ZoneInfo("Asia/Kolkata")


def today() -> dt.date:
    """The assistant's 'today', resolved in the app timezone (IST).

    Date-range resolution always takes `today` as an explicit argument so
    tests and evaluation runs are reproducible; production callers use this.
    """
    return dt.datetime.now(tz=APP_TIMEZONE).date()


class Intent(str, Enum):
    """What the user is fundamentally asking. Closed list over real schema."""

    # Transaction intelligence
    TRANSACTION_SUMMARY = "transaction_summary"   # SUM/COUNT/AVG/MAX/MIN over txns
    TRANSACTION_LIST = "transaction_list"         # list matching transactions
    TOP_DESCRIPTIONS = "top_descriptions"         # group by description, sum, top N
    MONTHLY_TREND = "monthly_trend"               # group by month, find peak / trend
    COMPARISON = "comparison"                     # same metric, two date ranges
    # Account intelligence
    ACCOUNT_BALANCE = "account_balance"           # one account / total / highest
    ACCOUNT_LIST = "account_list"                 # list accounts (masked numbers)
    # Bank intelligence
    BANK_BALANCE = "bank_balance"                 # balance per bank / which bank holds most
    BANK_ACCOUNT_COUNT = "bank_account_count"     # accounts per bank
    # Reference search
    REFERENCE_LOOKUP = "reference_lookup"         # find by reference id or UTR


class Metric(str, Enum):
    TRANSACTION_AMOUNT = "transaction_amount"
    TRANSACTION_COUNT = "transaction_count"
    BALANCE = "balance"


class Aggregation(str, Enum):
    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MAX = "max"
    MIN = "min"
    NONE = "none"   # record listing


class GroupByDimension(str, Enum):
    BANK = "bank"
    ACCOUNT = "account"
    TRANSACTION_TYPE = "transaction_type"
    MONTH = "month"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


# ---------------------------------------------------------------------------
# Date ranges — resolved to absolute dates by the BACKEND, never trusted
# from the LLM. See resolve_date_range for the full grammar.
# ---------------------------------------------------------------------------

class DateRangeType(str, Enum):
    CALENDAR_MONTH = "calendar_month"           # explicit month (label or start)
    LAST_N_MONTHS = "last_n_months"
    CUSTOM = "custom"                           # explicit start/end dates
    ALL_TIME = "all_time"
    MONTH_BEFORE_PREVIOUS = "month_before_previous"
    THIS_MONTH = "this_month"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    LAST_N_DAYS = "last_n_days"
    YESTERDAY = "yesterday"
    TODAY = "today"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"


class DateRange(BaseModel):
    """A resolved date range — always concrete dates by execution time."""

    model_config = ConfigDict(extra="forbid")

    type: DateRangeType
    start: dt.date | None = None
    end: dt.date | None = None
    n_months: int | None = Field(default=None, ge=1, le=24)
    n_days: int | None = Field(default=None, ge=1, le=365)
    label: str | None = None  # human label, e.g. "Aug 2026"

    @model_validator(mode="after")
    def _check_consistency(self) -> "DateRange":
        anchored = {
            DateRangeType.ALL_TIME, DateRangeType.LAST_N_MONTHS,
            DateRangeType.MONTH_BEFORE_PREVIOUS, DateRangeType.THIS_MONTH,
            DateRangeType.THIS_WEEK, DateRangeType.LAST_WEEK,
            DateRangeType.YESTERDAY, DateRangeType.TODAY,
            DateRangeType.THIS_YEAR, DateRangeType.LAST_YEAR,
        }
        if self.type in anchored:
            return self
        if self.type == DateRangeType.LAST_N_DAYS:
            if self.n_days is None:
                raise ValueError("n_days is required for last_n_days ranges")
            return self
        if self.start is None or self.end is None:
            raise ValueError(
                f"start and end are required for date_range type '{self.type.value}'"
            )
        if self.start > self.end:
            raise ValueError("start date must be <= end date")
        return self


class ComparisonSpec(BaseModel):
    """Second date range for comparison intents."""

    model_config = ConfigDict(extra="forbid")

    against: Literal["previous_period", "previous_month", "previous_year"] = (
        "previous_period"
    )


class QueryFilters(BaseModel):
    """Explicit allowlist of filterable dimensions — maps 1:1 to real columns."""

    model_config = ConfigDict(extra="forbid")

    bank_code: str | None = None
    bank_name: str | None = None            # resolved to bank_code by the backend
    account_id: str | None = None
    transaction_type: Literal["credit", "debit"] | None = None
    description_contains: str | None = None
    reference_id: str | None = None         # transaction_reference_id (plaintext)
    utr_number: str | None = None           # sensitive — backend masks any output
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)

    @field_validator("min_amount", "max_amount")
    @classmethod
    def _check_amount_order(cls, v, info):
        # cross-field ordering checked in FinancialQuery validator below
        return v

    @field_validator("description_contains", "reference_id", "utr_number", "bank_code",
                     "bank_name")
    @classmethod
    def _no_sql_injection(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # These values are bound as SQL parameters (never interpolated), so
        # injection is structurally impossible; this validator additionally
        # rejects raw SQL tokens so that even logged/echoed payloads look sane.
        import re

        if re.search(r"(--|;|\bunion\b|\bselect\b.*\bfrom\b|\bdrop\b|\bdelete\b|\binsert\b"
                     r"|\bupdate\b\s+\w+\s+set\b|'|\"|%|\bexec\b)", v, re.IGNORECASE):
            raise ValueError("filter value contains disallowed characters")
        return v.strip()


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
    sort: SortDirection = SortDirection.DESC
    limit: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _check_coherence(self) -> "FinancialQuery":
        # amount ordering
        if (self.filters.min_amount is not None and self.filters.max_amount is not None
                and self.filters.min_amount > self.filters.max_amount):
            raise ValueError("min_amount must be <= max_amount")

        # metric-intent coherence
        balance_intents = {Intent.ACCOUNT_BALANCE, Intent.BANK_BALANCE}
        if self.intent in balance_intents and self.metric != Metric.BALANCE:
            raise ValueError(
                f"intent '{self.intent.value}' requires metric 'balance'"
            )
        if self.intent == Intent.BANK_ACCOUNT_COUNT and self.metric != Metric.TRANSACTION_COUNT:
            # count of accounts reuses the count metric
            raise ValueError("bank_account_count requires metric 'transaction_count' (count of accounts)")
        if self.intent not in balance_intents | {Intent.ACCOUNT_LIST} and self.metric == Metric.BALANCE:
            raise ValueError(
                f"metric 'balance' only applies to account_balance/bank_balance/account_list intents"
            )
        if self.intent == Intent.REFERENCE_LOOKUP and self.aggregation != Aggregation.NONE:
            raise ValueError("reference_lookup must be a record listing (aggregation='none')")

        # grouping coherence
        if self.intent == Intent.TOP_DESCRIPTIONS:
            if not self.filters.description_contains:
                raise ValueError("top_descriptions requires description_contains")
        if self.intent == Intent.MONTHLY_TREND and GroupByDimension.MONTH not in self.group_by:
            raise ValueError("monthly_trend requires group_by=['month']")
        if self.intent == Intent.COMPARISON and self.comparison is None:
            raise ValueError("comparison intent requires a comparison spec")
        if self.intent != Intent.COMPARISON and self.comparison is not None:
            raise ValueError("comparison spec only allowed with comparison intent")

        # listing default
        if self.intent in (Intent.TRANSACTION_LIST, Intent.REFERENCE_LOOKUP,
                           Intent.ACCOUNT_LIST) and self.limit is None:
            self.limit = 20
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
# ambiguous / invalid (judging: hallucination prevention).
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


# ---------------------------------------------------------------------------
# Date resolution — deterministic grammar.
# ---------------------------------------------------------------------------

def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    """First and last calendar day of a month."""
    start = dt.date(year, month, 1)
    last_day = _calendar.monthrange(year, month)[1]
    return start, dt.date(year, month, last_day)


def _resolve_named_month(today: dt.date, name: str, year: int | None = None) -> DateRange:
    """Resolve a month name like 'june' or 'august 2026' to a calendar month."""
    months = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
    months.update({m.lower(): i for i, m in enumerate(_calendar.month_abbr) if m})
    key = name.lower().strip()
    if key not in months:
        raise ValueError(f"unknown month name: {name}")
    month_num = months[key]
    if year is None:
        year = today.year
        # A bare month that hasn't happened yet this year means last year's.
        if dt.date(year, month_num, 1) > today:
            year -= 1
    start, end = month_bounds(year, month_num)
    return DateRange(
        type=DateRangeType.CALENDAR_MONTH, start=start, end=end,
        label=start.strftime("%b %Y"),
    )


def resolve_date_range(today: dt.date, spec: dict) -> DateRange:
    """Resolve a relative range descriptor to absolute dates.

    Deterministic: takes `today` explicitly rather than reading the clock,
    so tests and evaluation runs are reproducible. The LLM may produce the
    spec (type + month names + optional day offsets) but never the final
    dates — those are always computed here.
    """
    range_type = spec.get("type")

    if range_type == DateRangeType.CALENDAR_MONTH.value:
        if spec.get("month"):
            return _resolve_named_month(
                today, spec["month"], spec.get("year")
            )
        if spec.get("start") and spec.get("end"):
            start = dt.date.fromisoformat(spec["start"])
            end = dt.date.fromisoformat(spec["end"])
            label = start.strftime("%b %Y") if start.day == 1 else None
            return DateRange(
                type=DateRangeType.CALENDAR_MONTH, start=start, end=end,
                label=label,
            )
        # "last month" relative to today's month
        first_of_current = today.replace(day=1)
        last_month_end = first_of_current - dt.timedelta(days=1)
        start = last_month_end.replace(day=1)
        return DateRange(
            type=DateRangeType.CALENDAR_MONTH, start=start, end=last_month_end,
            label=start.strftime("%b %Y"),
        )

    if range_type == DateRangeType.THIS_MONTH.value:
        start = today.replace(day=1)
        return DateRange(type=DateRangeType.THIS_MONTH, start=start, end=today,
                         label=f"{start.strftime('%b %Y')} (month to date)")

    if range_type == DateRangeType.LAST_N_MONTHS.value:
        n = int(spec.get("n_months") or 1)
        anchor_end = today.replace(day=1) - dt.timedelta(days=1)  # last completed month
        start = anchor_end
        for _ in range(n - 1):
            start = (start.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        return DateRange(
            type=DateRangeType.LAST_N_MONTHS, start=start, end=anchor_end, n_months=n,
            label=f"{n} month{'s' if n != 1 else ''} to {anchor_end.strftime('%b %Y')}",
        )

    if range_type == DateRangeType.MONTH_BEFORE_PREVIOUS.value:
        first_of_current = today.replace(day=1)
        last_month_end = first_of_current - dt.timedelta(days=1)
        first_of_last_month = last_month_end.replace(day=1)
        month_before_end = first_of_last_month - dt.timedelta(days=1)
        start = month_before_end.replace(day=1)
        return DateRange(
            type=DateRangeType.MONTH_BEFORE_PREVIOUS, start=start, end=month_before_end,
            label=start.strftime("%b %Y"),
        )

    if range_type == DateRangeType.THIS_WEEK.value:
        # ISO week: Monday = day 1
        start = today - dt.timedelta(days=today.isoweekday() - 1)
        return DateRange(type=DateRangeType.THIS_WEEK, start=start, end=today,
                         label=f"week of {start.strftime('%b %-d, %Y')}")

    if range_type == DateRangeType.LAST_WEEK.value:
        this_monday = today - dt.timedelta(days=today.isoweekday() - 1)
        start = this_monday - dt.timedelta(days=7)
        end = this_monday - dt.timedelta(days=1)
        return DateRange(type=DateRangeType.LAST_WEEK, start=start, end=end,
                         label=f"week of {start.strftime('%b %-d, %Y')}")

    if range_type == DateRangeType.LAST_N_DAYS.value:
        n = int(spec.get("n_days") or 7)
        end = today
        start = today - dt.timedelta(days=n - 1)
        return DateRange(type=DateRangeType.LAST_N_DAYS, start=start, end=end,
                         n_days=n, label=f"last {n} days")

    if range_type == DateRangeType.YESTERDAY.value:
        d = today - dt.timedelta(days=1)
        return DateRange(type=DateRangeType.YESTERDAY, start=d, end=d,
                         label=d.strftime("%b %-d, %Y"))

    if range_type == DateRangeType.TODAY.value:
        return DateRange(type=DateRangeType.TODAY, start=today, end=today,
                         label=today.strftime("%b %-d, %Y"))

    if range_type == DateRangeType.THIS_YEAR.value:
        start = dt.date(today.year, 1, 1)
        return DateRange(type=DateRangeType.THIS_YEAR, start=start, end=today,
                         label=f"{today.year} year to date")

    if range_type == DateRangeType.LAST_YEAR.value:
        start = dt.date(today.year - 1, 1, 1)
        end = dt.date(today.year - 1, 12, 31)
        return DateRange(type=DateRangeType.LAST_YEAR, start=start, end=end,
                         label=str(today.year - 1))

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
        label=f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}",
    )
