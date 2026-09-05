import uuid
import csv
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

CSV_EXTENSION = ".csv"


def load_csv(file_path: Path) -> List[Document]:
    """Load a CSV file where each row becomes its own Document.

    All column values are joined into the page_content so the LLM can read
    them naturally. Every column is also stored individually in metadata so
    callers can filter on specific fields.
    """
    documents = []
    with open(file_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            # Build human-readable content from all columns
            content = "\n".join(f"{k}: {v}" for k, v in row.items() if v is not None)
            metadata = {
                "source": str(file_path.name),
                "file_name": file_path.name,
                "row": i,
                **{k: str(v) for k, v in row.items()},
            }
            documents.append(Document(page_content=content, metadata=metadata))
    logger.info(f"Loaded {len(documents)} rows from CSV {file_path}")
    return documents


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
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        try:
            if suffix == CSV_EXTENSION:
                # Each CSV row becomes its own Document — no further chunking needed
                docs = load_csv(file_path)
                documents.extend(docs)
            elif suffix in LOADER_MAP:
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


def ingest_documents(
    data_dir: Optional[Path] = None, collection_name: Optional[str] = None
) -> int:
    """Full ingestion pipeline: load, chunk, and embed documents."""
    vector_store = get_vector_store()
    collection = vector_store.get_collection(collection_name)

    documents = load_documents(data_dir)
    if not documents:
        logger.warning("No documents to ingest")
        return 0

    # CSV rows are already one document per row — skip the chunker for those.
    # Non-CSV documents go through normal chunking.
    csv_docs = [
        d for d in documents if d.metadata.get("file_name", "").endswith(".csv")
    ]
    other_docs = [
        d for d in documents if not d.metadata.get("file_name", "").endswith(".csv")
    ]
    chunks = csv_docs + (chunk_documents(other_docs) if other_docs else [])

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


def ingest_texts(
    texts: List[str], metadatas: List[dict], collection_name: Optional[str] = None
) -> int:
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
