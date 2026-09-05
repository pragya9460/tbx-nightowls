"""Shared query result type for SQLAlchemy and DuckDB engines."""
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
