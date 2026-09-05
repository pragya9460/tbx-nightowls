"""Unified question endpoint + knowledge management routes.

POST /api/ask carries the rag-api request shape:
  {question, top_k, threshold, filter, query_type}

query_type:
  - "auto"      — grounded financial engine first; semantic knowledge path
                  only when the engine refuses (unsupported/ambiguous/invalid).
                  A real zero from the engine stays a real zero — never
                  silently re-answered from documents.
  - "analytics" — grounded engine only (answer/grounded ChatResponse)
  - "semantic"  — knowledge base only
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import config
from ..knowledge import config as kcfg
from ..knowledge import retriever as knowledge_retriever
from ..knowledge.ingestion import ingest_directory, ingest_texts
from ..knowledge.store import KnowledgeUnavailable, get_store
from ..services.chat_service import ChatService
from ..conversation.memory import ConversationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_conversation_store = ConversationStore()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    filter: dict[str, Any] | None = None
    query_type: Literal["auto", "analytics", "semantic"] = "auto"


class KnowledgeIngestTextRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    metadatas: list[dict[str, Any]] = Field(min_length=1)


def _ask_response(payload: dict, query_type: str) -> dict:
    """Map a ChatResponse-shaped dict to the unified ask shape."""
    return {
        "answer": payload.get("answer", ""),
        "query_type": query_type,
        "status": payload.get("status", "supported"),
        "confidence": payload.get("confidence"),
        "confidence_basis": payload.get("confidence_basis"),
        "evidence": payload.get("evidence"),
        "query": payload.get("query"),
        "refusal": payload.get("refusal"),
        "sources": [],
        "meta": payload.get("meta", {}),
    }


@router.post("/ask")
def ask(req: AskRequest) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")

    if req.query_type == "semantic":
        result = knowledge_retriever.ask(
            question, top_k=req.top_k, threshold=req.threshold, where=req.filter
        )
        return {**result, "query_type": "semantic"}

    # analytics / auto → grounded engine first
    engine_payload: dict | None = None
    try:
        service = ChatService()
        try:
            engine_payload = service.handle(
                question, None, _conversation_store
            )
        finally:
            service.close()
    except Exception as exc:
        logger.warning("grounded engine failed on ask: %s", exc)
        engine_payload = None

    engine_handled = (
        engine_payload is not None
        and engine_payload.get("status") in ("supported", "empty_data")
    )

    if req.query_type == "analytics" or engine_handled:
        return _ask_response(engine_payload or {
            "answer": "The financial engine could not process that question.",
            "status": "invalid",
            "confidence": "none",
            "meta": {"grounded": True, "backend": "mysql"},
        }, "analytics")

    # auto + engine refused (unsupported / ambiguous / invalid) → semantic path
    try:
        semantic = knowledge_retriever.ask(
            question, top_k=req.top_k, threshold=req.threshold, where=req.filter
        )
    except Exception as exc:
        logger.error("semantic path failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"ask failed: {exc}") from exc

    if semantic["status"] == "unavailable":
        # no knowledge base → surface the engine's original refusal, which is
        # the honest answer ("X data is not in the dataset")
        return _ask_response(engine_payload, "analytics")

    return {**semantic, "query_type": "semantic"}


# ----- knowledge management ---------------------------------------------------

@router.post("/knowledge/ingest")
def knowledge_ingest() -> dict:
    """(Re-)ingest every supported document under the knowledge data dir."""
    if not kcfg.KNOWLEDGE_DATA_DIR:
        raise HTTPException(
            status_code=400,
            detail="no knowledge data dir configured (ARTHA_KNOWLEDGE_DATA_DIR)",
        )
    try:
        count = ingest_directory()
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "chunks_ingested": count}


@router.post("/knowledge/ingest/text")
def knowledge_ingest_text(req: KnowledgeIngestTextRequest) -> dict:
    if len(req.texts) != len(req.metadatas):
        raise HTTPException(status_code=422, detail="texts and metadatas must have the same length")
    try:
        count = ingest_texts(req.texts, req.metadatas)
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "chunks_ingested": count}


@router.post("/knowledge/ingest/upload")
async def knowledge_ingest_upload(files: list[UploadFile] = File(...)) -> dict:
    if not kcfg.KNOWLEDGE_DATA_DIR:
        raise HTTPException(
            status_code=400,
            detail="no knowledge data dir configured (ARTHA_KNOWLEDGE_DATA_DIR)",
        )
    data_dir = Path(kcfg.KNOWLEDGE_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in kcfg.ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported file type '{suffix}' — allowed: {sorted(kcfg.ALLOWED_UPLOAD_EXTENSIONS)}",
            )
        payload = await f.read()
        if len(payload) > kcfg.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds {kcfg.MAX_UPLOAD_BYTES} bytes")
        (data_dir / f.filename).write_bytes(payload)
        saved += 1
    count = ingest_directory(data_dir)
    return {"status": "ok", "files_saved": saved, "chunks_ingested": count}


@router.get("/knowledge/search")
def knowledge_search(
    q: str,
    top_k: int | None = None,
    threshold: float | None = None,
) -> dict:
    """Raw similarity search — no LLM."""
    try:
        hits = knowledge_retriever.search(q, top_k=top_k, threshold=threshold)
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"results": hits, "count": len(hits)}


@router.get("/knowledge/collections")
def knowledge_collections() -> dict:
    try:
        store = get_store()
        count = store.count()
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "collections": [kcfg.CHROMA_COLLECTION_NAME],
        "documents": count,
        "enabled": kcfg.KNOWLEDGE_ENABLED,
        "embedding_model": kcfg.EMBEDDING_MODEL,
    }


@router.post("/knowledge/collections/{name}/reset")
def knowledge_reset(name: str) -> dict:
    if name != kcfg.CHROMA_COLLECTION_NAME:
        raise HTTPException(status_code=404, detail=f"unknown collection '{name}'")
    try:
        get_store().reset()
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "reset": name}
