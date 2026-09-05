"""Shared query result type for SQLAlchemy and DuckDB engines.

This is the GROUNDING CONTRACT — the single boundary between financial
computation and language generation:

    deterministic computation
            ↓
    QueryResult (verified: only values the database returned, masked)
            ↓
    evidence builder + answer generator   ← no DB access, no user input
            ↓
    answer + "how I got this" + source records

Rules that make the answer generator structurally incapable of becoming a
source of financial truth:

1. ``QueryResult`` is produced ONLY by the engines (``FinancialQueryEngine``,
   ``DuckDBQueryEngine``) after a validated ``FinancialQuery`` executed.
   Nothing else may construct one for the chat path.
2. Every value in ``summary``/``breakdown``/``records`` comes from SQL rows —
   never from the LLM, never from the user's question text.
3. Sensitive fields are masked by the engine BEFORE this object leaves the
   engine boundary; downstream code cannot un-mask (raw values are absent).
4. ``generate_answer()`` receives (validated query, QueryResult) and renders
   templates — it has no database session and cannot execute queries.
5. A test asserts rule 4 structurally (see tests/test_grounding_contract.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QueryResult:
    summary: dict = field(default_factory=dict)
    breakdown: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    query_metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "breakdown": self.breakdown,
            "records": self.records,
            "query_metadata": self.query_metadata,
        }
