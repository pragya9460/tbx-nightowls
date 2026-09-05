"""Answer generation from VERIFIED query results.

Answers are rendered from backend-computed values with deterministic
templates. The LLM is never asked to produce or restate numbers — grounding
is structural, not prompted.
"""
from __future__ import annotations

import datetime as dt

from ..query_engine.engine import QueryResult
from ..schemas.query import FinancialQuery, Metric

INR_CRORE = 10_000_000
INR_LAKH = 100_000


def format_inr(value: float) -> str:
    """Human INR formatting: ₹12.84 lakh / ₹1.2 crore / ₹4,500."""
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= INR_CRORE:
        return f"{sign}₹{v / INR_CRORE:,.2f} crore"
    if v >= INR_LAKH:
        return f"{sign}₹{v / INR_LAKH:,.2f} lakh"
    return f"{sign}₹{v:,.0f}"


def _month_label(dr_dict: dict) -> str:
    label = dr_dict.get("label")
    if label:
        return label
    if dr_dict.get("start"):
        try:
            start = dt.date.fromisoformat(dr_dict["start"])
            end = dr_dict.get("end")
            if end and end[:7] != start.isoformat()[:7]:
                return f"{start.strftime('%b %-d')} – {dt.date.fromisoformat(end).strftime('%b %-d, %Y')}"
            return start.strftime("%B %Y")
        except (ValueError, TypeError):
            pass
    return "the selected period"


def generate_answer(q: FinancialQuery, result: QueryResult,
                    comparison_result: QueryResult | None = None) -> str:
    """Deterministic answer text from computed values."""
    metric = q.metric
    dr = q.date_range.model_dump(mode="json", exclude_none=True)
    period = _month_label(dr)
    vendor = q.filters.vendor_name

    if q.aggregation.value == "none":
        # list question
        matched = result.summary.get("record_count") or len(result.records)
        shown = len(result.records)
        status = q.filters.reconciliation_status or "unreconciled"
        if matched == 0:
            return f"No {status} transactions found for {period}."
        suffix = (
            f" Showing the {shown} most recent." if shown < matched else ""
        )
        return (
            f"Found {matched:,} {status} transaction{'s' if matched != 1 else ''} "
            f"for {period}.{suffix}"
        )

    value = result.summary.get("value") or 0
    count = result.summary.get("record_count", 0)

    # Grouped questions (top vendors)
    if q.group_by:
        if metric == Metric.PAYOUT_AMOUNT:
            if not result.breakdown:
                return f"No vendor payouts found for {period}."
            top = result.breakdown[0]
            lines = ", ".join(
                f"{b.get('vendor_name', 'Unknown')} ({format_inr(b.get('value') or 0)})"
                for b in result.breakdown[:3]
            )
            return (
                f"Total payouts for {period}: {format_inr(value)}. "
                f"Top vendors were {lines}. Full breakdown below."
            )
        return f"Found {count} groups for {period}."

    # Comparison
    if comparison_result is not None:
        prev_value = comparison_result.summary.get("value") or 0
        label_b = comparison_result.query_metadata.get("date_range", {}).get("label") or "the previous period"
        if prev_value == 0:
            return (
                f"{format_inr(value)} for {period} — no comparable spend in {label_b}."
            )
        pct = ((value - prev_value) / prev_value) * 100
        direction = "up" if pct >= 0 else "down"
        return (
            f"{format_inr(value)} for {period} vs {format_inr(prev_value)} for {label_b} — "
            f"that's {direction} {abs(pct):.1f}%."
        )

    # Vendor-specific
    if q.intent.value == "vendor_spend" and vendor:
        if value == 0:
            return (
                f"No payouts found for {vendor} in {period}. "
                f"That vendor may not have been paid in this period."
            )
        return (
            f"You paid {vendor} {format_inr(value)} in {period}"
            f" across {count} payout{'s' if count != 1 else ''}."
        )

    # Count questions
    if metric in (Metric.PAYOUT_COUNT, Metric.TRANSACTION_COUNT):
        status = q.filters.reconciliation_status
        if status:
            return (
                f"{value:,} {status} transaction{'s' if value != 1 else ''} in {period}."
            )
        return f"{value:,} transactions in {period}."

    # Generic payout summary
    if value == 0:
        return f"No vendor payouts recorded for {period}."
    return (
        f"You spent {format_inr(value)} on vendor payouts in {period}"
        f" across {count} payout{'s' if count != 1 else ''}."
    )
