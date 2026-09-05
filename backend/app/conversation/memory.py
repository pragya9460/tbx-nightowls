"""Lightweight session-based conversation memory.

Stores STRUCTURED context (last intent, metric, transaction type, filters,
date range) — not the raw transcript. Follow-ups like "what about July?" or
"which bank contributed the most?" resolve against this explicit state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.query import FinancialQuery


@dataclass
class ConversationContext:
    last_intent: str | None = None
    last_metric: str | None = None
    last_transaction_type: str | None = None
    last_bank: str | None = None
    last_date_range: dict | None = None
    last_filters: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def apply(self, q: FinancialQuery, answer_value=None) -> None:
        self.last_intent = q.intent.value
        self.last_metric = q.metric.value
        self.last_transaction_type = q.filters.transaction_type or self.last_transaction_type
        self.last_bank = q.filters.bank_code or self.last_bank
        self.last_date_range = q.date_range.model_dump(mode="json", exclude_none=True)
        self.last_filters = {k: v for k, v in q.filters.model_dump().items() if v is not None}
        self.history.append({"answer_summary": answer_value})
        # keep memory bounded
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def apply_scenario(self, scenario: str, answer_summary: str) -> None:
        """Record a Financial Twin scenario turn (not a FinancialQuery)."""
        self.last_intent = f"scenario:{scenario}"
        self.history.append({"answer_summary": answer_summary})
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def to_prompt_context(self) -> dict:
        """Compact context passed to the LLM for follow-up resolution."""
        return {
            "last_intent": self.last_intent,
            "last_metric": self.last_metric,
            "last_transaction_type": self.last_transaction_type,
            "last_bank": self.last_bank,
            "last_date_range": self.last_date_range,
            "last_filters": self.last_filters,
        }

    def is_empty(self) -> bool:
        return self.last_intent is None


class ConversationStore:
    """In-memory session store keyed by conversation id. Swap for Redis/MySQL
    persistence later without touching the API layer."""

    def __init__(self):
        self._conversations: dict[str, ConversationContext] = {}

    def get(self, conversation_id: str | None) -> ConversationContext:
        if not conversation_id:
            return ConversationContext()
        return self._conversations.setdefault(conversation_id, ConversationContext())

    def reset(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)
