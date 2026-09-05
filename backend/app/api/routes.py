"""REST endpoints — MySQL-backed chat and structured query."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config
from ..conversation.memory import ConversationStore
from ..db import build_engine, masked_default_url
from ..db_settings import (
    clear_session_database_url,
    resolve_database_url,
    set_session_database_url,
)
from ..query_engine.mysql_engine import MySQLQueryEngine
from ..query_engine.mysql_url import mask_mysql_url
from ..schemas.query import (
    FinancialQuery,
    QueryRefusalReason,
    resolve_comparison_range,
    refusal,
    today as app_today,
)
from ..services.chat_service import ChatService
from .schemas import ChatRequest, ChatResponse, HealthResponse, QueryRequest

router = APIRouter(prefix="/api")

conversation_store = ConversationStore()


def _engine(conversation_id: str | None = None) -> MySQLQueryEngine:
    url = resolve_database_url(conversation_id)
    return build_engine(url)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    counts: dict[str, int] = {}
    db_ok = True
    try:
        eng = _engine()
        try:
            for table in ("bank", "account", "transaction"):
                counts[table] = eng.count_total(table)
        finally:
            eng.close()
    except Exception:
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "error",
        backend="mysql",
        database_url_masked=masked_default_url(),
        llm_provider=config.effective_provider(),
        model=config.effective_model()
        if config.effective_provider() in ("anthropic", "ollama")
        else None,
        record_counts=counts,
    )


class DatabaseSettingsRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    database_url: str | None = Field(
        default=None,
        description="mysql:// URL; omit or empty to clear session override",
    )


class DatabaseSettingsResponse(BaseModel):
    conversation_id: str
    database_url_masked: str
    backend: str = "mysql"
    using_session_override: bool


@router.post("/settings/database", response_model=DatabaseSettingsResponse)
def set_database(req: DatabaseSettingsRequest) -> DatabaseSettingsResponse:
    """Set or clear a per-conversation MySQL URL (judge demo override)."""
    from ..db_settings import get_session_database_url

    try:
        if not req.database_url or not req.database_url.strip():
            clear_session_database_url(req.conversation_id)
            return DatabaseSettingsResponse(
                conversation_id=req.conversation_id,
                database_url_masked=masked_default_url(),
                using_session_override=False,
            )
        set_session_database_url(req.conversation_id, req.database_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    url = get_session_database_url(req.conversation_id)
    assert url
    try:
        eng = build_engine(url)
        try:
            if not eng.ping():
                clear_session_database_url(req.conversation_id)
                raise HTTPException(status_code=400, detail="could not connect to MySQL")
            eng.count_total("bank")
        finally:
            eng.close()
    except HTTPException:
        raise
    except Exception as e:
        clear_session_database_url(req.conversation_id)
        raise HTTPException(
            status_code=400, detail=f"MySQL connection failed: {e}"
        ) from e

    return DatabaseSettingsResponse(
        conversation_id=req.conversation_id,
        database_url_masked=mask_mysql_url(url),
        using_session_override=True,
    )



@router.get("/settings/database/{conversation_id}", response_model=DatabaseSettingsResponse)
def get_database(conversation_id: str) -> DatabaseSettingsResponse:
    from ..db_settings import get_session_database_url

    session = get_session_database_url(conversation_id)
    if session:
        return DatabaseSettingsResponse(
            conversation_id=conversation_id,
            database_url_masked=mask_mysql_url(session),
            using_session_override=True,
        )
    return DatabaseSettingsResponse(
        conversation_id=conversation_id,
        database_url_masked=masked_default_url(),
        using_session_override=False,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    try:
        url = resolve_database_url(req.conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    service = ChatService(database_url=url)
    try:
        payload = service.handle(req.question, req.conversation_id, conversation_store)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chat processing failed: {e}") from e
    finally:
        service.close()
    return ChatResponse(**payload)


@router.post("/query", response_model=ChatResponse)
def run_query(req: QueryRequest) -> ChatResponse:
    """Execute a structured query directly — no LLM. Useful for tests/eval."""
    service = ChatService()
    try:
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
                meta={"grounded": False, "backend": "mysql"},
            )

        comparison_result = None
        if fq.intent.value == "comparison":
            base = service.engine.execute(fq)
            cmp_dr = resolve_comparison_range(app_today(), fq.date_range, fq.comparison)
            comparison_result = service.engine.execute_comparison(
                fq, base, cmp_dr.start, cmp_dr.end
            )
            result = base
        else:
            result = service.engine.execute(fq)

        from ..query_engine.evidence import build_evidence
        from ..services.answers import generate_answer

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
            meta={"grounded": True, "provider": "none", "backend": "mysql"},
        )
    finally:
        service.close()
