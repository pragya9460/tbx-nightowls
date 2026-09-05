"""API request/response contracts (strongly typed)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class QueryRequest(BaseModel):
    """Direct structured-query execution (bypasses the LLM entirely)."""

    intent: str
    metric: str
    aggregation: str
    filters: dict[str, Any] = Field(default_factory=dict)
    date_range: dict[str, Any]
    group_by: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=100)
    comparison: dict[str, Any] | None = None


class EvidenceExportRequest(BaseModel):
    """Rows to export — verbatim from an evidence block's records/breakdown.

    The client sends back exactly what the UI displayed, so the export can
    never contain data the user wasn't already shown (masking is upstream).
    """

    rows: list[dict[str, Any]] = Field(min_length=1)
    format: Literal["csv", "excel"] = "csv"


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    evidence: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    refusal: dict[str, Any] | None = None
    # Query state (Must-Have 5 + confidence bonus): one of
    # supported | empty_data | ambiguous | unsupported | invalid.
    status: Literal["supported", "empty_data", "ambiguous", "unsupported",
                    "invalid"] = "supported"
    # Interpretable confidence signal — NOT a statistical probability.
    # high   = exact supported query, computed deterministically
    # medium = valid query, but zero records matched (a real zero)
    # none   = nothing executed (refusal path)
    confidence: Literal["high", "medium", "none"] = "high"
    confidence_basis: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "error"]
    llm_provider: str
    model: str | None
    record_counts: dict[str, int] = Field(default_factory=dict)
