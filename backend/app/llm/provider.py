"""LLM abstraction for query understanding.

LLMProvider
    ├── AnthropicProvider    (claude-haiku-4-5 by default — smallest capable model)
    └── RuleBasedProvider    (deterministic fallback, no API key needed)

The provider's ONLY job is to map a user question + conversation context to a
structured query descriptor. It never produces numbers; those come from the
query engine.
"""
from __future__ import annotations

import datetime as dt
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..schemas.query import (
    Aggregation,
    ComparisonSpec,
    FinancialQuery,
    Intent,
    Metric,
    QueryFilters,
    resolve_date_range,
)


@dataclass
class QueryUnderstanding:
    """What a provider returns: a raw dict to be validated as FinancialQuery,
    or an explicit refusal/clarification."""

    query: dict | None = None
    refusal_reason: str | None = None      # unsupported | ambiguous | invalid
    refusal_message: str | None = None
    suggestions: list[str] = field(default_factory=list)
    model_used: str | None = None
    provider_used: str | None = None
    latency_ms: int | None = None


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        ...


# ---------------------------------------------------------------------------
# Helpers shared by providers
# ---------------------------------------------------------------------------

_VENDOR_HINTS = ["supplier", "vendor", "paid to", "pay to", "payout to"]


def _looks_like_payout(q: str) -> bool:
    return any(k in q for k in ("payout", "paid", "spend", "spent", "pay", "expense", "vendor"))


def _looks_like_reconciliation(q: str) -> bool:
    return any(k in q for k in ("unreconciled", "reconcil", "outstanding", "mismatch"))


def _extract_vendor_name(question: str, vendor_names: list[str]) -> str | None:
    ql = question.lower()
    best = None
    for name in vendor_names:
        if name.lower() in ql:
            if best is None or len(name) > len(best):
                best = name
    return best


class RuleBasedProvider(LLMProvider):
    """Deterministic question→query mapper. Guarantees a working demo without
    an API key and doubles as the evaluation baseline."""

    name = "rule_based"

    def __init__(self, vendor_names: list[str] | None = None):
        self.vendor_names = vendor_names or []

    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        q = question.lower().strip()
        context = context or {}

        # Unsupported domains — refuse instead of inventing.
        unsupported_patterns = [
            (r"salaries?|payroll|employees?|wages?", "employee payroll data is not available in the current financial dataset"),
            (r"tax(es)?|gst|income tax", "tax data is not available in the current financial dataset"),
            (r"revenue|sales|income|invoice[s]? received|receivable", "revenue/sales data is not available — the dataset covers spend, vendor payouts and reconciliation only"),
            (r"profit|margin|balance sheet", "profit/margin data is not available — the dataset covers spend, vendor payouts and reconciliation only"),
            (r"forecast|project", "forecasting is not supported yet"),
        ]
        for pat, msg in unsupported_patterns:
            if re.search(pat, q):
                return QueryUnderstanding(
                    refusal_reason="unsupported",
                    refusal_message=f"I can't answer that reliably because {msg}.",
                    suggestions=[
                        "How much did we spend on vendor payouts last month?",
                        "Which vendors received the most money last month?",
                        "How many transactions were unreconciled last month?",
                    ],
                    provider_used=self.name,
                )

        today = dt.date.today()

        # Date range parsing
        range_spec = {"type": "calendar_month"}  # default: last month
        if re.search(r"month before|two months ago|prior.*month", q):
            range_spec = {"type": "month_before_previous"}
        elif re.search(r"last (\d+) months?|past (\d+) months?", q):
            m = re.search(r"last (\d+) months?|past (\d+) months?", q)
            n = int(m.group(1) or m.group(2))
            range_spec = {"type": "last_n_months", "n_months": n}
        elif re.search(r"this month|current month", q):
            first = today.replace(day=1)
            range_spec = {"type": "custom", "start": first.isoformat(), "end": today.isoformat()}
        elif re.search(r"all time|ever", q):
            range_spec = {"type": "all_time"}
        elif re.search(r"compare|versus|vs|month before that|the month before", q):
            range_spec = {"type": "calendar_month"}  # base range; comparison resolved later

        vendor_name = _extract_vendor_name(q, self.vendor_names)

        # ----- reconciliation questions -------------------------------------
        if _looks_like_reconciliation(q):
            if re.search(r"how many|count|number of", q):
                return QueryUnderstanding(
                    query=self._validated(
                        intent=Intent.TRANSACTION_COUNT,
                        metric=Metric.TRANSACTION_COUNT,
                        aggregation=Aggregation.COUNT,
                        filters={"reconciliation_status": "unreconciled"},
                        range_spec=range_spec,
                    ),
                    provider_used=self.name,
                )
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.UNRECONCILED_LIST,
                    metric=Metric.TRANSACTION_COUNT,
                    aggregation=Aggregation.NONE,
                    filters={"reconciliation_status": "unreconciled"},
                    range_spec=range_spec,
                ),
                provider_used=self.name,
            )

        # ----- top vendors ----------------------------------------------------
        if re.search(r"top|most (money|amount)|highest", q) and _looks_like_payout(q):
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.TOP_VENDORS,
                    metric=Metric.PAYOUT_AMOUNT,
                    aggregation=Aggregation.SUM,
                    filters={},
                    range_spec=range_spec,
                    group_by=["vendor"],
                    limit=10,
                ),
                provider_used=self.name,
            )

        # ----- comparison follow-up -------------------------------------------
        if re.search(r"compare|comparison|versus|\bvs\b|month before that|the month before", q):
            prev_intent = context.get("last_intent")
            base_range = context.get("last_date_range") or {"type": "calendar_month"}
            # The comparison base is the *previous* answer's metric; range is
            # the month before the base range.
            metric = Metric.PAYOUT_AMOUNT
            intent = Intent.VENDOR_PAYOUT_SUMMARY
            if prev_intent == Intent.UNRECONCILED_LIST.value:
                metric = Metric.TRANSACTION_COUNT
                intent = Intent.UNRECONCILED_LIST
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.COMPARISON,
                    metric=metric,
                    aggregation=Aggregation.SUM if metric == Metric.PAYOUT_AMOUNT else Aggregation.COUNT,
                    filters=context.get("last_filters") or {},
                    range_spec=base_range,
                    comparison={"against": "previous_period"},
                ),
                provider_used=self.name,
            )

        # ----- vendor-specific spend ------------------------------------------
        if vendor_name and _looks_like_payout(q):
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.VENDOR_SPEND,
                    metric=Metric.PAYOUT_AMOUNT,
                    aggregation=Aggregation.SUM,
                    filters={"vendor_name": vendor_name},
                    range_spec=range_spec,
                ),
                provider_used=self.name,
            )

        # ----- count questions --------------------------------------------------
        if re.search(r"how many", q) and not _looks_like_reconciliation(q):
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.TRANSACTION_COUNT,
                    metric=Metric.TRANSACTION_COUNT,
                    aggregation=Aggregation.COUNT,
                    filters={},
                    range_spec=range_spec,
                ),
                provider_used=self.name,
            )

        # ----- generic payout summary -------------------------------------------
        if _looks_like_payout(q):
            # "how much did we spend" without specifying what — ambiguous
            if re.search(r"how much did we spend(?! on)", q) and not vendor_name \
               and not re.search(r"payout|vendor", q):
                return QueryUnderstanding(
                    refusal_reason="ambiguous",
                    refusal_message=(
                        "What would you like me to calculate? 'Spend' could refer to "
                        "vendor payouts or transactions."
                    ),
                    suggestions=[
                        "How much did we spend on vendor payouts last month?",
                        "How much did we spend on transactions last month?",
                    ],
                    provider_used=self.name,
                )
            metric = Metric.PAYOUT_AMOUNT
            aggregation = Aggregation.SUM
            if re.search(r"how many (payouts|payments)", q):
                metric = Metric.PAYOUT_COUNT
                aggregation = Aggregation.COUNT
            return QueryUnderstanding(
                query=self._validated(
                    intent=Intent.VENDOR_PAYOUT_SUMMARY,
                    metric=metric,
                    aggregation=aggregation,
                    filters={},
                    range_spec=range_spec,
                ),
                provider_used=self.name,
            )

        # ----- fallback: cannot map ----------------------------------------------
        return QueryUnderstanding(
            refusal_reason="unsupported",
            refusal_message=(
                "I couldn't map that question to a supported financial query. "
                "I can answer questions about vendor payouts, spend by vendor, "
                "top vendors, and unreconciled transactions."
            ),
            suggestions=[
                "How much did we spend on vendor payouts last month?",
                "Which transactions are still unreconciled?",
                "Which vendors received the most money last month?",
            ],
            provider_used=self.name,
        )

    def _validated(
        self, intent: Intent, metric: Metric, aggregation: Aggregation,
        filters: dict, range_spec: dict, group_by: list[str] | None = None,
        comparison: dict | None = None, limit: int | None = None,
    ) -> dict:
        try:
            dr = resolve_date_range(dt.date.today(), range_spec)
        except Exception:
            dr = resolve_date_range(dt.date.today(), {"type": "calendar_month"})
        payload = {
            "intent": intent.value,
            "metric": metric.value,
            "aggregation": aggregation.value,
            "filters": filters,
            "date_range": dr.model_dump(mode="json", exclude_none=True),
            "group_by": group_by or [],
        }
        if limit is not None:
            payload["limit"] = limit
        if comparison:
            payload["comparison"] = comparison
        return payload


# ---------------------------------------------------------------------------
# Anthropic provider (loaded lazily so the module imports without the SDK)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the query-understanding module of a finance assistant.
Convert the user's question into a single structured financial query, as strict JSON.

Allowed intents: vendor_payout_summary, unreconciled_list, vendor_spend, top_vendors,
transaction_count, comparison.

Rules:
- If the question asks about anything outside this list (payroll, revenue, taxes,
  profit, forecasts), refuse with {"refusal": "unsupported", "message": "..."}.
- If the question is too vague to determine the metric (e.g. "how much did we
  spend" with no subject), refuse with {"refusal": "ambiguous", "message": "..."}.
- For date ranges, output {"type": "calendar_month"} for "last month",
  {"type": "last_n_months", "n_months": N} for "last N months",
  {"type": "custom", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} for explicit dates.
  Do NOT compute dates yourself.
- For comparisons ("how does that compare with the month before"), use intent
  "comparison" and set "comparison": {"against": "previous_period"}, reusing the
  metric/filters from the previous answer given in the context.
- For vendor-specific questions, set filters.vendor_name to the vendor's name.
- For top-vendor questions use intent "top_vendors" with group_by ["vendor"].
- For unreconciled questions use intent "unreconciled_list" (list) or
  "transaction_count" with filters.reconciliation_status "unreconciled" (count).
- metric: payout_amount | payout_count | transaction_amount | transaction_count.
- aggregation: sum | count | none. "none" only with unreconciled_list.
- Output ONLY JSON. No prose, no markdown fences."""

QUERY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "refusal": {"type": "string", "enum": ["unsupported", "ambiguous"]},
        "message": {"type": "string"},
        "intent": {
            "type": "string",
            "enum": [i.value for i in Intent],
        },
        "metric": {
            "type": "string",
            "enum": [m.value for m in Metric],
        },
        "aggregation": {
            "type": "string",
            "enum": [a.value for a in Aggregation if a != Aggregation.NONE],
        },
        "filters": {
            "type": "object",
            "properties": {
                "vendor_id": {"type": "string"},
                "vendor_name": {"type": "string"},
                "payout_status": {"type": "string", "enum": ["paid", "pending", "failed"]},
                "reconciliation_status": {
                    "type": "string", "enum": ["reconciled", "unreconciled", "pending"]
                },
                "transaction_category": {"type": "string"},
                "vendor_category": {"type": "string"},
                "account": {"type": "string"},
                "transaction_type": {"type": "string", "enum": ["debit", "credit"]},
            },
            "additionalProperties": False,
        },
        "date_range": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["calendar_month", "last_n_months", "custom", "all_time",
                             "month_before_previous"],
                },
                "n_months": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "group_by": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["vendor", "vendor_category", "category", "account",
                         "payout_status", "reconciliation_status", "month"],
            },
        },
        "limit": {"type": "integer"},
        "comparison": {
            "type": "object",
            "properties": {"against": {
                "type": "string", "enum": ["previous_period", "previous_month", "previous_year"],
            }},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_retries: int = 1, timeout: float = 30.0):
        import anthropic  # lazy import

        self.client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries,
        )
        self.model = model

    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        import time

        context = context or {}
        started = time.monotonic()

        context_block = ""
        if context:
            context_block = (
                "\nPrevious turn context (use for follow-ups like 'compare that' or "
                "'what about that vendor'):\n"
                + json_dumps(context)
            )

        messages = [
            {"role": "user", "content": f"{context_block}\n\nQuestion: {question}"},
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": QUERY_JSON_SCHEMA}},
            )
        except Exception as e:  # network/auth errors — degrade to refusal, not crash
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message=(
                    "The question-understanding service is temporarily unavailable. "
                    f"({type(e).__name__})"
                ),
                provider_used=self.name,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        text = next((b.text for b in response.content if b.type == "text"), "")
        latency = int((time.monotonic() - started) * 1000)

        try:
            import json

            data = json.loads(text)
        except Exception:
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message="I couldn't interpret that question reliably.",
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        if data.get("refusal"):
            return QueryUnderstanding(
                refusal_reason=data["refusal"],
                refusal_message=data.get("message", "I can't answer that."),
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        return QueryUnderstanding(
            query=data,
            provider_used=self.name,
            model_used=self.model,
            latency_ms=latency,
        )


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, default=str)


def build_provider(
    provider_name: str, api_key: str, model: str,
    vendor_names: list[str] | None = None,
    max_retries: int = 1, timeout: float = 30.0,
) -> LLMProvider:
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model,
                                 max_retries=max_retries, timeout=timeout)
    if provider_name == "rule_based":
        return RuleBasedProvider(vendor_names=vendor_names)
    raise ValueError(f"unknown provider: {provider_name}")
