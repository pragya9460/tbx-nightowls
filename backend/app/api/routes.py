"""REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import config
from ..conversation.memory import ConversationStore
from ..db import get_db
from ..query_engine.engine import FinancialQueryEngine
from ..schemas.query import FinancialQuery, refusal, QueryRefusalReason
from ..services.chat_service import ChatService
from .schemas import ChatRequest, ChatResponse, HealthResponse, QueryRequest

router = APIRouter(prefix="/api")

# Single process-wide conversation store; keyed by conversation_id.
conversation_store = ConversationStore()


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    counts: dict[str, int] = {}
    db_ok = True
    try:
        engine = FinancialQueryEngine(db)
        for table in ("vendors", "transactions", "vendor_payouts", "reconciliation"):
            counts[table] = engine.count_total(table)
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "error",
        llm_provider=config.effective_provider(),
        model=config.LLM_MODEL if config.effective_provider() == "anthropic" else None,
        record_counts=counts,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    service = ChatService(db)
    try:
        payload = service.handle(req.question, req.conversation_id, conversation_store)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chat processing failed: {e}")
    return ChatResponse(**payload)


@router.post("/query", response_model=ChatResponse)
def run_query(req: QueryRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """Execute a structured query directly — no LLM in the loop. Useful for
    testing, the evaluation harness, and programmatic clients."""
    service = ChatService(db)
    try:
        fq = FinancialQuery.model_validate(req.model_dump())
    except Exception as e:
        r = refusal(
            QueryRefusalReason.INVALID_STRUCTURE,
            f"Invalid structured query: {e}",
        )
        return ChatResponse(
            conversation_id="direct",
            answer=r.message,
            refusal=r.model_dump(),
            meta={"grounded": False},
        )
    result = service.engine.execute(fq)
    from ..query_engine.evidence import build_evidence
    from ..services.answers import generate_answer

    comparison_result = None
    if fq.intent.value == "comparison":
        import datetime as dt

        from ..schemas.query import previous_period

        base = service.engine.execute(fq)
        pp = previous_period(fq.date_range)
        comparison_result = service.engine.execute_comparison(
            fq, base, pp.start, pp.end
        )
        result = base

    answer = generate_answer(fq, result, comparison_result)
    evidence = build_evidence(fq, result)
    if comparison_result is not None:
        evidence["comparison"] = build_evidence(fq, comparison_result)

    return ChatResponse(
        conversation_id="direct",
        answer=answer,
        evidence=evidence,
        query=fq.model_dump(mode="json", exclude_none=True),
        refusal=None,
        meta={"grounded": True, "provider": "none"},
    )
