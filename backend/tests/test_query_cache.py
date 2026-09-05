"""Query result cache tests (in-process MemoryCache when Redis is unset)."""
from __future__ import annotations

from app.query_engine import cache as cache_mod
from app.query_engine.cache import cache_key_for_query, get_cached_result, put_cached_result
from app.schemas.query import (
    Aggregation,
    DateRangeType,
    FinancialQuery,
    Intent,
    Metric,
)


def _q() -> FinancialQuery:
    return FinancialQuery.model_validate({
        "intent": Intent.TRANSACTION_SUMMARY.value,
        "metric": Metric.TRANSACTION_AMOUNT.value,
        "aggregation": Aggregation.SUM.value,
        "filters": {"transaction_type": "debit"},
        "date_range": {
            "type": DateRangeType.CUSTOM.value,
            "start": "2026-08-01",
            "end": "2026-08-31",
        },
    })


def test_cache_key_stable():
    a = _q()
    b = FinancialQuery.model_validate(a.model_dump(mode="json"))
    assert cache_key_for_query(a) == cache_key_for_query(b)


def test_execute_caches_result(duck_engine, monkeypatch):
    cache_mod.reset_cache_backend()
    monkeypatch.setattr("app.config.QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr("app.config.QUERY_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr("app.config.REDIS_URL", "")

    q = _q()
    first = duck_engine.execute(q)
    assert first.query_metadata.get("cache_hit") is False
    assert first.summary["value"] > 0

    second = duck_engine.execute(q)
    assert second.query_metadata.get("cache_hit") is True
    assert second.summary["value"] == first.summary["value"]


def test_put_get_roundtrip(monkeypatch):
    cache_mod.reset_cache_backend()
    monkeypatch.setattr("app.config.QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr("app.config.REDIS_URL", "")

    from app.query_engine.result import QueryResult

    q = FinancialQuery.model_validate({
        "intent": "transaction_summary",
        "metric": "transaction_count",
        "aggregation": "count",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "custom", "start": "2025-01-01", "end": "2025-01-31"},
    })
    result = QueryResult(
        summary={"value": 42.0, "record_count": 1},
        query_metadata={"backend": "mysql", "sql": "SELECT 1"},
    )
    put_cached_result(q, result)
    hit = get_cached_result(q)
    assert hit is not None
    assert hit.query_metadata["cache_hit"] is True
    assert hit.summary["value"] == 42.0
