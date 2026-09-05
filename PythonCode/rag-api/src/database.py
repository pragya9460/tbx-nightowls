from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions
from src.config import get_settings, settings
import logging

logger = logging.getLogger(__name__)


class VectorStore:
    """Vector store client wrapper for ChromaDB."""

    def __init__(self):
        self._client = None
        self._collection = None
        self._embedding_function = None

    def _get_embedding_function(self):
        """Get ChromaDB-compatible embedding function using sentence-transformers."""
        if self._embedding_function is None:
            self._embedding_function = (
                embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
            )
        return self._embedding_function

    def get_client(self) -> chromadb.Client:
        """Get or create ChromaDB client."""
        if self._client is None:
            persist_dir = Path(settings.chroma_persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info(f"ChromaDB client initialized at {persist_dir}")
        return self._client

    def get_collection(self, name: str | None = None):
        """Get or create collection."""
        if self._collection is None or (
            name and name != settings.chroma_collection_name
        ):
            client = self.get_client()
            collection_name = name or settings.chroma_collection_name
            self._collection = client.get_or_create_collection(
                name=collection_name,
                embedding_function=self._get_embedding_function(),
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Collection '{collection_name}' ready")
        return self._collection

    def reset_collection(self, name: str | None = None):
        """Delete and recreate collection."""
        client = self.get_client()
        collection_name = name or settings.chroma_collection_name
        try:
            client.delete_collection(name=collection_name)
            logger.info(f"Deleted collection '{collection_name}'")
        except Exception:
            pass
        self._collection = None
        return self.get_collection(name)


vector_store = VectorStore()


def get_vector_store() -> VectorStore:
    """Dependency injection for vector store."""
    return vector_store
