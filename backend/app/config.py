"""Application settings, resolved from environment variables."""
from __future__ import annotations

import os

# MySQL connection string (required). Example:
# mysql://artha:artha@127.0.0.1:3306/artha
DATABASE_URL: str = os.environ.get(
    "ARTHA_DATABASE_URL",
    os.environ.get("ARTHA_MYSQL_URL", "mysql://artha:artha@127.0.0.1:3306/artha"),
).strip()

# CSV directory used when bootstrapping MySQL.
DATA_DIR: str = os.environ.get("ARTHA_DATA_DIR", "")

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# Provider: "ollama" (local LLM), "anthropic", or "rule_based" (no LLM).
# Empty = anthropic if ANTHROPIC_API_KEY is set, else rule_based.
LLM_PROVIDER: str = os.environ.get("ARTHA_LLM_PROVIDER", "")

# Model id for the active provider (Ollama tag or Anthropic model name).
LLM_MODEL: str = os.environ.get(
    "ARTHA_MODEL",
    os.environ.get("ARTHA_LLM_MODEL", "claude-haiku-4-5"),
)

# Ollama OpenAI-compatible / native API base (no trailing slash).
OLLAMA_BASE_URL: str = os.environ.get(
    "ARTHA_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
).rstrip("/")

LLM_MAX_RETRIES: int = int(os.environ.get("ARTHA_LLM_MAX_RETRIES", "1"))
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("ARTHA_LLM_TIMEOUT", "60"))

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

# Semantic knowledge layer (RAG) — disabled unless ARTHA_KNOWLEDGE_ENABLED=1.
# See app/knowledge/config.py for its own settings.


def effective_provider() -> str:
    if LLM_PROVIDER:
        return LLM_PROVIDER
    return "anthropic" if ANTHROPIC_API_KEY else "rule_based"


def effective_model() -> str:
    """Model string for the active provider (sensible Ollama default)."""
    if effective_provider() == "ollama" and (
        not LLM_MODEL or LLM_MODEL.startswith("claude")
    ):
        return os.environ.get("ARTHA_MODEL", "qwen2.5-coder:7b")
    return LLM_MODEL or "claude-haiku-4-5"
