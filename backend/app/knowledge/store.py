"""ChromaDB vector store wrapper — lazy, single collection, cosine space.

Embeddings run locally (ONNX MiniLM via chromadb's default embedding
function). Nothing here touches MySQL or the financial engine.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from . import config as kcfg

logger = logging.getLogger(__name__)


class KnowledgeUnavailable(RuntimeError):
    """Raised when the semantic layer can't serve (disabled / no data / no model)."""


class VectorStore:
    def __init__(self) -> None:
        self._client: Any = None
        self._collection: Any = None
        self._embed_fn: Any = None
        self._lock = threading.Lock()

    def _embedding_function(self):
        if self._embed_fn is None:
            from chromadb.utils import embedding_functions

            self._embed_fn = (
                embedding_functions.ONNXMiniLM_L6_V2()
                if hasattr(embedding_functions, "ONNXMiniLM_L6_V2")
                else embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=kcfg.EMBEDDING_MODEL
                )
            )
        return self._embed_fn

    def _ensure(self):
        if self._collection is not None:
            return self._collection
        with self._lock:
            if self._collection is not None:
                return self._collection
            if not kcfg.KNOWLEDGE_ENABLED:
                raise KnowledgeUnavailable(
                    "the semantic knowledge layer is disabled "
                    "(set ARTHA_KNOWLEDGE_ENABLED=1)"
                )
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            persist = Path(kcfg.CHROMA_PERSIST_DIR)
            persist.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(persist),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=kcfg.CHROMA_COLLECTION_NAME,
                embedding_function=self._embedding_function(),
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "ChromaDB ready at %s (collection=%s, docs=%s)",
                persist, kcfg.CHROMA_COLLECTION_NAME, self._collection.count(),
            )
        return self._collection

    def count(self) -> int:
        try:
            return self._ensure().count()
        except KnowledgeUnavailable:
            raise
        except Exception as exc:  # model download failure, corrupt store, …
            raise KnowledgeUnavailable(f"vector store unavailable: {exc}") from exc

    def add(self, texts: list[str], metadatas: list[dict], ids: list[str]) -> int:
        col = self._ensure()
        if not texts:
            return 0
        col.add(documents=texts, metadatas=metadatas, ids=ids)
        return len(texts)

    def query(
        self,
        text: str,
        top_k: int,
        threshold: float,
        where: dict | None = None,
    ) -> list[dict]:
        """Similarity search → [{content, metadata, similarity}] above threshold."""
        col = self._ensure()
        n = min(top_k, col.count()) if col.count() else 0
        if n == 0:
            return []
        kwargs: dict[str, Any] = {"query_texts": [text], "n_results": n}
        if where:
            kwargs["where"] = where
        res = col.query(**kwargs)
        out: list[dict] = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            similarity = 1.0 - float(dists[i]) if i < len(dists) else 0.0
            if similarity < threshold:
                continue
            out.append({
                "content": doc,
                "metadata": metas[i] if i < len(metas) else {},
                "similarity": round(similarity, 4),
            })
        return out

    def reset(self) -> None:
        with self._lock:
            if self._client is None:
                return
            try:
                self._client.delete_collection(kcfg.CHROMA_COLLECTION_NAME)
            except Exception:
                pass
            self._collection = None
        self._ensure()


_store = VectorStore()


def get_store() -> VectorStore:
    return _store
