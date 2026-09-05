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


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    evidence: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    refusal: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "error"]
    backend: Literal["mysql"] = "mysql"
    database_url_masked: str | None = None
    llm_provider: str
    model: str | None
    record_counts: dict[str, int] = Field(default_factory=dict)
