"""Answer generation from VERIFIED query results.

Answers are rendered from backend-computed values with deterministic
templates. The LLM is never asked to produce or restate numbers — grounding
is structural, not prompted.
"""
from __future__ import annotations

import datetime as dt

from ..query_engine.result import QueryResult
from ..schemas.query import FinancialQuery, Metric

INR_CRORE = 10_000_000
INR_LAKH = 100_000


def format_inr(value: float) -> str:
    """Indian-format rupee amounts: ₹1,24,850 / ₹12.84 lakh / ₹1.2 crore."""
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= INR_CRORE:
        return f"{sign}₹{v / INR_CRORE:,.2f} crore"
    if v >= INR_LAKH:
        return f"{sign}₹{v / INR_LAKH:,.2f} lakh"
    return f"{sign}₹{_indian_number(v)}"


def _indian_number(v: float) -> str:
    """Indian digit grouping: 1,24,850 not 124,850."""
    s = f"{v:,.0f}".replace(",", "")
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _period_label(dr_dict: dict) -> str:
    label = dr_dict.get("label")
    if label:
        return label
    if dr_dict.get("start"):
        try:
            start = dt.date.fromisoformat(dr_dict["start"][:10])
            end = dr_dict.get("end")
            if end and end[:10] != start.isoformat():
                end_d = dt.date.fromisoformat(end[:10])
                if start.month == end_d.month and start.year == end_d.year:
                    return start.strftime("%B %Y")
                return f"{start.strftime('%b %-d')} – {end_d.strftime('%b %-d, %Y')}"
            return start.strftime("%B %Y")
        except (ValueError, TypeError):
            pass
    return "the selected period"


def _type_phrase(q: FinancialQuery) -> str:
    """'debit transactions' / 'credit transactions' / 'transactions'."""
    t = q.filters.transaction_type
    if t == "debit":
        return "debit transactions"
    if t == "credit":
        return "credit transactions"
    return "transactions"


def generate_answer(q: FinancialQuery, result: QueryResult,
                    comparison_result: QueryResult | None = None) -> str:
    """Deterministic answer text from computed values."""
    dr = q.date_range.model_dump(mode="json", exclude_none=True)
    period = _period_label(dr)
    value = result.summary.get("value") or 0
    count = result.summary.get("record_count", 0)
    f = q.filters

    # ----- reference lookup -------------------------------------------------
    if q.intent.value == "reference_lookup":
        if count == 0:
            which = "UTR" if f.utr_number else "reference"
            ref = f.utr_number or f.reference_id or ""
            return (
                f"No transaction found with {which} ending in "
                f"...{ref[-4:] if ref else ''}. Reference IDs and UTRs are "
                f"matched exactly against the database."
            )
        return f"Found {count} transaction{'s' if count != 1 else ''} matching that reference. Details below."

    # ----- balance / accounts / banks ---------------------------------------
    if q.intent.value == "account_balance":
        if result.summary.get("not_found"):
            return "I couldn't find that account in the dataset."
        if q.filters.account_id:
            return f"Available balance: {format_inr(value)}."
        if GroupBy_check(q, "account"):
            top = result.breakdown[0] if result.breakdown else None
            if top:
                return (
                    f"Highest balance: {format_inr(top['value'])} in account "
                    f"{top['account_number']} ({top['bank_name']})."
                )
            return "No accounts found in the dataset."
        return f"Total available balance across {count} account{'s' if count != 1 else ''}: {format_inr(value)}."

    if q.intent.value == "bank_balance":
        if q.group_by:
            top = result.breakdown[0] if result.breakdown else None
            if top:
                return (
                    f"{top['bank_name']} holds the most: {format_inr(top['value'])} "
                    f"across {top.get('account_count', 1)} account"
                    f"{'s' if top.get('account_count', 1) != 1 else ''}."
                )
            return "No accounts found in the dataset."
        return f"Total balance in the selected bank(s): {format_inr(value)} across {count} account{'s' if count != 1 else ''}."

    if q.intent.value == "account_list":
        if count == 0:
            return "No accounts found."
        return f"You have {count} account{'s' if count != 1 else ''}. Listed below with balances."

    if q.intent.value == "bank_account_count":
        top = result.breakdown[0] if result.breakdown else None
        if top:
            lines = ", ".join(
                f"{b['bank_name']} ({b['value']})" for b in result.breakdown[:4]
            )
            return f"Accounts by bank: {lines}."
        return "No accounts found."

    if q.intent.value == "bank_count":
        if value == 0:
            return "No banks found in the dataset."
        if q.filters.bank_code or q.filters.bank_name:
            return f"That bank is in the dataset — {count} matching bank entr{'y' if count == 1 else 'ies'} found."
        return f"There are {count} bank{'s' if count != 1 else ''} in the dataset."

    # ----- monthly trend ------------------------------------------------------
    if q.intent.value == "monthly_trend":
        peak = result.summary.get("peak_month")
        if peak:
            month_name = dt.date.fromisoformat(peak + "-01").strftime("%B %Y")
            metric_word = "spend" if f.transaction_type == "debit" else (
                "inflow" if f.transaction_type == "credit" else "activity"
            )
            return (
                f"{month_name} had the highest {metric_word}: "
                f"{format_inr(value)}. Monthly breakdown below."
            )
        return f"No transactions found for {period}."

    # ----- listing -------------------------------------------------------------
    if q.aggregation.value == "none":
        matched = count or len(result.records)
        shown = len(result.records)
        if matched == 0:
            subject = f.description_contains or "matching"
            return f"No {subject} transactions found for {period}."
        if f.reference_id or f.utr_number:
            return (f"Found {matched} matching transaction"
                    f"{'s' if matched != 1 else ''}. Details below.")
        suffix = f" Showing the {shown} most relevant." if shown < matched else ""
        desc = f" containing “{f.description_contains}”" if f.description_contains else ""
        amount = (
            f" above {format_inr(f.min_amount)}" if f.min_amount is not None
            else f" up to {format_inr(f.max_amount)}" if f.max_amount is not None
            else ""
        )
        return (
            f"Found {matched:,} {f.transaction_type or ''} "
            f"transaction{'s' if matched != 1 else ''}{desc}{amount} "
            f"for {period}.{suffix}"
        )

    # ----- comparison ------------------------------------------------------------
    if comparison_result is not None:
        prev_value = comparison_result.summary.get("value") or 0
        label_b = comparison_result.query_metadata.get("date_range", {}).get("label") \
            or "the previous period"
        if prev_value == 0:
            return f"{format_inr(value)} for {period} — no comparable amount in {label_b}."
        pct = ((value - prev_value) / prev_value) * 100
        direction = "up" if pct >= 0 else "down"
        verb = "spent" if f.transaction_type == "debit" else "received"
        return (
            f"{format_inr(value)} {verb} in {period} vs {format_inr(prev_value)} "
            f"in {label_b} — that's {direction} {abs(pct):.1f}%."
        )

    # ----- grouped results (top descriptions / by bank / by month) --------------
    if q.group_by:
        if not result.breakdown:
            return f"No transactions found for {period}."
        lines = ", ".join(
            f"{_group_label(b, q)} ({format_inr(b.get('value') or 0)})"
            for b in result.breakdown[:3]
        )
        return (
            f"Total for {period}: {format_inr(value)}. "
            f"Top: {lines}. Full breakdown below."
        )

    # ----- spend / inflow summary -------------------------------------------------
    if f.transaction_type == "debit":
        verb, type_word = "spent", "debit"
    elif f.transaction_type == "credit":
        verb, type_word = "received", "credit"
    else:
        verb, type_word = "transacted", ""
    if value == 0 and count == 0:
        return f"No {type_word} transactions recorded for {period}.".strip()
    type_suffix = f" {type_word}" if type_word else ""
    if q.metric == Metric.TRANSACTION_COUNT:
        # A count is not money — never render it with ₹.
        period_phrase = "" if dr.get("type") == "all_time" else f" in {period}"
        return (
            f"You made {count:,}{type_suffix} transaction"
            f"{'s' if count != 1 else ''}{period_phrase}."
        )
    return (
        f"You {verb} {format_inr(value)} in {period} across "
        f"{count:,}{type_suffix} transaction{'s' if count != 1 else ''}."
    )


def _group_label(b: dict, q: FinancialQuery) -> str:
    if q.group_by and "bank_name" in b:
        return b["bank_name"]
    if q.group_by and "transaction_type" in b:
        return b["transaction_type"]
    return b.get("description", "Other")


def GroupBy_check(q: FinancialQuery, dim: str) -> bool:
    return any(d.value == dim for d in q.group_by)
