"""REST endpoints — MySQL-backed chat and structured query."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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
from ..services.confidence import confidence_for_result
from ..services.financial_twin import FinancialTwinEngine
from .schemas import ChatRequest, ChatResponse, EvidenceExportRequest, HealthResponse, QueryRequest

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
                status="invalid",
                confidence="none",
                confidence_basis=(
                    "the structured query failed validation; nothing was executed"
                ),
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

        empty = (
            result.summary.get("record_count") == 0
            and not result.records
            and not result.breakdown
        )
        status = "empty_data" if empty else "supported"
        confidence, confidence_basis = confidence_for_result(result)

        return ChatResponse(
            conversation_id="direct",
            answer=answer,
            evidence=evidence,
            query=fq.model_dump(mode="json", exclude_none=True),
            refusal=None,
            status=status,
            confidence=confidence,
            confidence_basis=confidence_basis,
            meta={"grounded": True, "provider": "none", "backend": "mysql"},
        )
    finally:
        service.close()


@router.post("/export/evidence")
def export_evidence(req: EvidenceExportRequest) -> StreamingResponse:
    """Export an evidence block's records/breakdown exactly as displayed.

    The rows are passed through verbatim (masked, capped — the same list the
    UI rendered), so the export can never contain more sensitive detail than
    the user already saw.
    """
    rows = req.rows
    if not rows:
        raise HTTPException(status_code=422, detail="no rows to export")
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    fmt = (req.format or "csv").lower()
    if fmt == "excel":
        try:
            from openpyxl import Workbook
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Excel export needs the openpyxl package installed",
            )
        wb = Workbook()
        ws = wb.active
        ws.title = "evidence"
        ws.append(columns)
        for row in rows:
            ws.append([row.get(c) for c in columns])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="artha_evidence.xlsx"'},
        )

    # default: CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="artha_evidence.csv"'},
    )


# ---------------------------------------------------------------------------
# Financial Twin endpoints (Phases 5–8). All deterministic; the LLM is not
# involved in any of these computations.
# ---------------------------------------------------------------------------

def _twin() -> "FinancialTwinEngine":
    eng = _engine()
    return FinancialTwinEngine(eng)


@router.get("/twin/accounts")
def twin_accounts() -> dict:
    twin = _twin()
    try:
        return twin.accounts_overview()
    finally:
        twin.db.close()


@router.get("/twin/rules")
def twin_rules() -> dict:
    twin = _twin()
    try:
        return twin.rules_and_reserves()
    finally:
        twin.db.close()


@router.get("/twin/vendors")
def twin_vendors(limit: int = 10) -> dict:
    twin = _twin()
    try:
        return twin.vendor_profiles(limit=min(limit, 100))
    finally:
        twin.db.close()


@router.get("/twin/reconciliation")
def twin_reconciliation() -> dict:
    twin = _twin()
    try:
        return twin.reconciliation_status()
    finally:
        twin.db.close()


@router.get("/twin/cash-position")
def twin_cash_position() -> dict:
    twin = _twin()
    try:
        return twin.cash_position()
    finally:
        twin.db.close()


@router.get("/twin/anomalies")
def twin_anomalies(limit: int = 5) -> dict:
    """Scan the most recent transactions against per-counterparty history.

    Deterministic rule: amount > multiplier × counterparty average
    (excluding the transaction itself), with a minimum history size.
    """
    twin = _twin()
    try:
        from ..services.anomaly import DEFAULT_MIN_HISTORY, DEFAULT_MULTIPLIER

        return {
            "anomalies": twin.scan_anomalies(limit=limit),
            "rule": {
                "type": "amount_vs_counterparty_average",
                "multiplier": DEFAULT_MULTIPLIER,
                "min_history": DEFAULT_MIN_HISTORY,
            },
            "provenance": "DERIVED",
            "note": (
                "deterministic rule on dataset amounts — the LLM never "
                "decides what is anomalous"
            ),
        }
    finally:
        twin.db.close()


class AffordabilityRequest(ChatRequest):
    pass


@router.get("/twin/afford")
def twin_afford(vendor: str, amount: float) -> dict:
    """Deterministic feasibility analysis — never executes a payment."""
    twin = _twin()
    try:
        return twin.can_i_afford(vendor, amount)
    finally:
        twin.db.close()


@router.get("/twin/simulate")
def twin_simulate(vendor: str, amount: float) -> dict:
    """Deterministic what-if: before → payment → after + rule outcomes."""
    twin = _twin()
    try:
        return twin.simulate_payment(vendor, amount)
    finally:
        twin.db.close()

