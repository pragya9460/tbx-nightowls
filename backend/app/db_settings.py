"""Per-conversation database URL overrides (judge demo settings)."""
from __future__ import annotations

import threading
from typing import Any

from .query_engine.mysql_url import mask_mysql_url, normalize_mysql_url, parse_mysql_url

_lock = threading.Lock()
_session_urls: dict[str, str] = {}


def set_session_database_url(conversation_id: str, url: str) -> dict[str, Any]:
    if not conversation_id or conversation_id == "anonymous":
        raise ValueError("conversation_id is required to store a database URL")
    normalized = normalize_mysql_url(url)
    parse_mysql_url(normalized)  # validate
    with _lock:
        _session_urls[conversation_id] = normalized
    return {
        "conversation_id": conversation_id,
        "database_url_masked": mask_mysql_url(normalized),
        "backend": "mysql",
    }


def clear_session_database_url(conversation_id: str) -> None:
    with _lock:
        _session_urls.pop(conversation_id, None)


def get_session_database_url(conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    with _lock:
        return _session_urls.get(conversation_id)


def resolve_database_url(
    conversation_id: str | None = None,
    *,
    env_url: str | None = None,
) -> str:
    """session override → env URL (no DuckDB fallback)."""
    from . import config

    session = get_session_database_url(conversation_id)
    if session:
        return session
    url = (env_url if env_url is not None else config.DATABASE_URL).strip()
    if not url:
        raise ValueError(
            "ARTHA_DATABASE_URL is not set. Configure a MySQL connection string."
        )
    return normalize_mysql_url(url)
