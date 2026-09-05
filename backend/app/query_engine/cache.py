"""Query result cache — Redis when available, in-process fallback otherwise.

Cache key = SHA-256 of the canonical FinancialQuery JSON (same DSL → same
result). Values are serialized QueryResult dicts. Safe for read-only finance
data with a short TTL.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from typing import Any, Protocol

from .. import config
from ..schemas.query import FinancialQuery
from .result import QueryResult


class CacheBackend(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict, ttl: int) -> None: ...


class MemoryCache:
    """Process-local TTL cache (used when Redis is unset or unreachable)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.monotonic():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: dict, ttl: int) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)


class RedisCache:
    def __init__(self, url: str):
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        # Fail fast if Redis is down so we can fall back.
        self._client.ping()

    def get(self, key: str) -> dict | None:
        raw = self._client.get(key)
        if not raw:
            return None
        return json.loads(raw)

    def set(self, key: str, value: dict, ttl: int) -> None:
        self._client.setex(key, ttl, json.dumps(value, default=str))


_backend: CacheBackend | None = None
_backend_lock = threading.Lock()


def _build_backend() -> CacheBackend:
    url = (config.REDIS_URL or "").strip()
    if url:
        try:
            return RedisCache(url)
        except Exception:
            # Redis optional — never block answers on cache outage.
            pass
    return MemoryCache()


def get_cache_backend() -> CacheBackend:
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = _build_backend()
        return _backend


def reset_cache_backend() -> None:
    """Test helper: drop the singleton so the next call rebuilds."""
    global _backend
    with _backend_lock:
        _backend = None


def cache_key_for_query(q: FinancialQuery, scope: str | None = None) -> str:
    payload = q.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    prefix = f"artha:query:{scope}:" if scope else "artha:query:"
    return f"{prefix}{digest}"


def result_from_cache_dict(data: dict[str, Any]) -> QueryResult:
    return QueryResult(
        summary=data.get("summary") or {},
        breakdown=data.get("breakdown") or [],
        records=data.get("records") or [],
        query_metadata=data.get("query_metadata") or {},
    )


def result_to_cache_dict(result: QueryResult) -> dict[str, Any]:
    # Deep snapshot: the cache must never alias a live QueryResult, or a
    # caller mutating their copy would corrupt the cached financial values.
    return copy.deepcopy(result.to_dict())


def get_cached_result(q: FinancialQuery, scope: str | None = None) -> QueryResult | None:
    if not config.QUERY_CACHE_ENABLED:
        return None
    key = cache_key_for_query(q, scope=scope)
    data = get_cache_backend().get(key)
    if not data:
        return None
    result = result_from_cache_dict(copy.deepcopy(data))
    result.query_metadata = dict(result.query_metadata)
    result.query_metadata["cache_hit"] = True
    return result


def put_cached_result(
    q: FinancialQuery, result: QueryResult, scope: str | None = None
) -> None:
    if not config.QUERY_CACHE_ENABLED:
        return
    key = cache_key_for_query(q, scope=scope)
    payload = result_to_cache_dict(result)
    # Don't persist a previous hit flag into the store.
    meta = dict(payload.get("query_metadata") or {})
    meta.pop("cache_hit", None)
    meta["cache_hit"] = False
    payload["query_metadata"] = meta
    get_cache_backend().set(key, payload, config.QUERY_CACHE_TTL_SECONDS)
