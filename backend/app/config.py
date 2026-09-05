"""Application settings, resolved from environment variables."""
from __future__ import annotations

import os

# DuckDB file path (default: <repo>/data/finance.duckdb).
DUCKDB_PATH: str = os.environ.get("ARTHA_DUCKDB_PATH", "")

# CSV directory used when bootstrapping DuckDB.
DATA_DIR: str = os.environ.get("ARTHA_DATA_DIR", "")

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# Provider selection: "anthropic" (LLM query understanding) or "rule_based"
# (deterministic fallback used when no API key is configured).
LLM_PROVIDER: str = os.environ.get("ARTHA_LLM_PROVIDER", "")

# Default to the smallest model that reliably handles structured extraction.
LLM_MODEL: str = os.environ.get("ARTHA_MODEL", "claude-haiku-4-5")

LLM_MAX_RETRIES: int = int(os.environ.get("ARTHA_LLM_MAX_RETRIES", "1"))
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("ARTHA_LLM_TIMEOUT", "30"))

CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get(
        "ARTHA_CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",")
    if o.strip()
]

API_TITLE = "Artha — AI Finance Assistant"
API_VERSION = "0.1.0"

# Query result cache: Redis when ARTHA_REDIS_URL is set, else in-process memory.
REDIS_URL: str = os.environ.get("ARTHA_REDIS_URL", "")
QUERY_CACHE_ENABLED: bool = os.environ.get("ARTHA_QUERY_CACHE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
QUERY_CACHE_TTL_SECONDS: int = int(os.environ.get("ARTHA_QUERY_CACHE_TTL", "300"))


def effective_provider() -> str:
    if LLM_PROVIDER:
        return LLM_PROVIDER
    return "anthropic" if ANTHROPIC_API_KEY else "rule_based"
