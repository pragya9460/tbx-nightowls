import os
import uuid
from pathlib import Path
from typing import List, Optional
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredMarkdownLoader,
)
from langchain.schema import Document
from src.config import settings
from src.database import get_vector_store
import logging

logger = logging.getLogger(__name__)

LOADER_MAP = {
    ".pdf": PyPDFLoader,
    ".txt": TextLoader,
    ".md": UnstructuredMarkdownLoader,
    ".docx": UnstructuredWordDocumentLoader,
    ".doc": UnstructuredWordDocumentLoader,
}


def get_loader(file_path: Path) -> Optional[object]:
    """Get appropriate document loader for file extension."""
    suffix = file_path.suffix.lower()
    loader_class = LOADER_MAP.get(suffix)
    if loader_class:
        return loader_class(str(file_path))
    logger.warning(f"No loader for extension: {suffix}")
    return None


def load_documents(data_dir: Optional[Path] = None) -> List[Document]:
    """Load all documents from data directory."""
    data_dir = data_dir or settings.data_dir
    documents = []

    if not data_dir.exists():
        logger.warning(f"Data directory does not exist: {data_dir}")
        return documents

    for file_path in data_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in LOADER_MAP:
            try:
                loader = get_loader(file_path)
                if loader:
                    docs = loader.load()
                    for doc in docs:
                        doc.metadata["source"] = str(file_path.relative_to(data_dir))
                        doc.metadata["file_name"] = file_path.name
                    documents.extend(docs)
                    logger.info(f"Loaded {len(docs)} documents from {file_path}")
            except Exception as e:
                logger.error(f"Failed to load {file_path}: {e}")

    return documents


def chunk_documents(documents: List[Document]) -> List[Document]:
    """Split documents into chunks."""
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = text_splitter.split_documents(documents)
    logger.info(f"Split {len(documents)} documents into {len(chunks)} chunks")
    return chunks


def ingest_documents(data_dir: Optional[Path] = None, collection_name: Optional[str] = None) -> int:
    """Full ingestion pipeline: load, chunk, and embed documents."""
    vector_store = get_vector_store()
    collection = vector_store.get_collection(collection_name)

    documents = load_documents(data_dir)
    if not documents:
        logger.warning("No documents to ingest")
        return 0

    chunks = chunk_documents(documents)

    texts = [chunk.page_content for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]
    ids = [str(uuid.uuid4()) for _ in chunks]

    collection.add(
        documents=texts,
        metadatas=metadatas,
        ids=ids,
    )
    logger.info(f"Added {len(chunks)} chunks to vector store")
    return len(chunks)


def ingest_texts(texts: List[str], metadatas: List[dict], collection_name: Optional[str] = None) -> int:
    """Ingest raw texts with metadata."""
    vector_store = get_vector_store()
    collection = vector_store.get_collection(collection_name)

    chunks = []
    for text, metadata in zip(texts, metadatas):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        doc_chunks = splitter.create_documents([text], metadatas=[metadata])
        chunks.extend(doc_chunks)

    texts = [chunk.page_content for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]
    ids = [str(uuid.uuid4()) for _ in chunks]

    collection.add(
        documents=texts,
        metadatas=metadatas,
        ids=ids,
    )
    return len(chunks)