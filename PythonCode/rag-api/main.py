from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uvicorn
import logging
from pathlib import Path
import shutil

from src.config import settings, get_settings
from src.ingestion import ingest_documents, ingest_texts, load_documents
from src.retriever import RAGRetriever, get_retriever
from src.database import get_vector_store
from scalar_fastapi import get_scalar_api_reference

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting RAG API...")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield
    logger.info("Shutting down RAG API...")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/documentation",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str = Field(..., description="Question to ask the RAG system")
    top_k: Optional[int] = Field(
        default=None, description="Number of results to retrieve"
    )
    threshold: Optional[float] = Field(default=None, description="Similarity threshold")
    filter: Optional[Dict[str, Any]] = Field(
        default=None, description="Metadata filter"
    )


class QueryResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = []


class IngestResponse(BaseModel):
    message: str
    chunks_ingested: int


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="healthy", version=settings.app_version)


@app.post("/query", response_model=QueryResponse)
async def query_rag(
    request: QueryRequest,
    retriever: RAGRetriever = Depends(get_retriever),
):
    """Query the RAG system."""
    try:
        result = retriever.query_with_sources(
            question=request.question,
            filter_dict=request.filter,
        )
        return QueryResponse(
            answer=result["answer"],
            sources=result["sources"],
        )
    except Exception as e:
        logger.error(f"Query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search")
async def similarity_search(
    request: QueryRequest,
    retriever: RAGRetriever = Depends(get_retriever),
):
    """Perform similarity search without LLM generation."""
    try:
        docs = retriever.similarity_search(
            query=request.question,
            top_k=request.top_k,
            threshold=request.threshold,
            filter_dict=request.filter,
        )
        return {
            "results": [
                {
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "similarity": doc.metadata.get("similarity", 0),
                }
                for doc in docs
            ]
        }
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest", response_model=IngestResponse)
async def ingest_data(
    collection_name: Optional[str] = Form(default=None),
    retriever: RAGRetriever = Depends(get_retriever),
):
    """Ingest documents from data directory."""
    try:
        count = ingest_documents(collection_name=collection_name)
        return IngestResponse(
            message=f"Successfully ingested {count} chunks",
            chunks_ingested=count,
        )
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/upload", response_model=IngestResponse)
async def upload_and_ingest(
    files: List[UploadFile] = File(...),
    collection_name: Optional[str] = Form(default=None),
):
    """Upload and ingest files."""
    try:
        uploaded_count = 0
        for file in files:
            file_path = settings.data_dir / file.filename
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            uploaded_count += 1

        count = ingest_documents(collection_name=collection_name)
        return IngestResponse(
            message=f"Uploaded {uploaded_count} files, ingested {count} chunks",
            chunks_ingested=count,
        )
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/text", response_model=IngestResponse)
async def ingest_text(
    texts: List[str],
    metadatas: List[Dict[str, Any]],
    collection_name: Optional[str] = None,
):
    """Ingest raw texts with metadata."""
    try:
        if len(texts) != len(metadatas):
            raise HTTPException(
                status_code=400, detail="texts and metadatas must have same length"
            )

        count = ingest_texts(texts, metadatas, collection_name)
        return IngestResponse(
            message=f"Successfully ingested {count} chunks from text",
            chunks_ingested=count,
        )
    except Exception as e:
        logger.error(f"Text ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/collection/{collection_name}")
async def delete_collection(collection_name: str):
    """Delete a collection."""
    try:
        vector_store = get_vector_store()
        vector_store.reset_collection(collection_name)
        return {"message": f"Collection '{collection_name}' deleted and recreated"}
    except Exception as e:
        logger.error(f"Delete collection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collections")
async def list_collections():
    """List all collections."""
    try:
        vector_store = get_vector_store()
        client = vector_store.get_client()
        collections = client.list_collections()
        return {"collections": [c.name for c in collections]}
    except Exception as e:
        logger.error(f"List collections failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scalar", include_in_schema=False)
def scalar():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url, title="Scalar Documents"
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
