"""Knowledge-layer settings (env-backed, mirrors app.config style)."""
from __future__ import annotations

import os


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in (
        "0", "false", "no", "off", "",
    )


# Master switch — the semantic layer stays inert unless explicitly enabled,
# so the grounded demo stack is unchanged by default.
KNOWLEDGE_ENABLED: bool = _flag("ARTHA_KNOWLEDGE_ENABLED", "0")

# Where documents are read from (seeded at startup) and uploaded docs land.
KNOWLEDGE_DATA_DIR: str = os.environ.get("ARTHA_KNOWLEDGE_DATA_DIR", "")

# ChromaDB persistence (inside the backend container by default).
CHROMA_PERSIST_DIR: str = os.environ.get(
    "ARTHA_CHROMA_PERSIST_DIR", "/app/knowledge-store"
)

CHROMA_COLLECTION_NAME: str = os.environ.get(
    "ARTHA_CHROMA_COLLECTION", "artha_knowledge"
)

# all-MiniLM-L6-v2 runs locally via ONNX (chromadb's default downloader) —
# no API key, no network at query time once the model is cached.
EMBEDDING_MODEL: str = os.environ.get(
    "ARTHA_EMBEDDING_MODEL", "all-MiniLM-L6-v2"
)

# Retrieval defaults (overridable per request).
TOP_K_DEFAULT: int = int(os.environ.get("ARTHA_KNOWLEDGE_TOP_K", "5"))
SIMILARITY_THRESHOLD_DEFAULT: float = float(
    os.environ.get("ARTHA_KNOWLEDGE_THRESHOLD", "0.3")
)
CHUNK_SIZE: int = int(os.environ.get("ARTHA_KNOWLEDGE_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.environ.get("ARTHA_KNOWLEDGE_CHUNK_OVERLAP", "200"))

# Columns that must NEVER enter the vector store in raw form.
SENSITIVE_COLUMNS = {"account_number", "utr_number"}

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
