"""REST endpoints — DuckDB-backed chat and structured query."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import config
from ..conversation.memory import ConversationStore
from ..query_engine.duckdb_engine import DuckDBQueryEngine
from ..schemas.query import FinancialQuery, QueryRefusalReason, previous_period, refusal
from ..services.chat_service import ChatService
from ..services.confidence import confidence_for_result
from ..services.financial_twin import FinancialTwinEngine
from .schemas import ChatRequest, ChatResponse, EvidenceExportRequest, HealthResponse, QueryRequest

router = APIRouter(prefix="/api")

conversation_store = ConversationStore()


def _engine() -> DuckDBQueryEngine:
    return DuckDBQueryEngine.from_path(config.DUCKDB_PATH or None)


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
        llm_provider=config.effective_provider(),
        model=config.LLM_MODEL if config.effective_provider() == "anthropic" else None,
        record_counts=counts,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    service = ChatService()
    try:
        payload = service.handle(req.question, req.conversation_id, conversation_store)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chat processing failed: {e}")
    finally:
        service.engine.close()
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
                meta={"grounded": False, "backend": "duckdb"},
            )

        comparison_result = None
        if fq.intent.value == "comparison":
            base = service.engine.execute(fq)
            pp = previous_period(fq.date_range)
            comparison_result = service.engine.execute_comparison(
                fq, base, pp.start, pp.end
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
            meta={"grounded": True, "provider": "none", "backend": "duckdb"},
        )
    finally:
        service.engine.close()


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
        from ..services.anomaly import evaluate_transaction
        from ..services.vendor_intel import extract_counterparty

        con = twin.db._con
        rows = con.execute(
            """
            SELECT description, transaction_amount, transaction_date
            FROM "transaction"
            WHERE description IS NOT NULL
            ORDER BY transaction_date DESC
            LIMIT 500
            """
        ).fetchall()
        cols = ["description", "transaction_amount", "transaction_date"]
        txns = [dict(zip(cols, r)) for r in rows]

        # group history by counterparty
        history: dict[str, list[dict]] = {}
        for t in txns:
            cp = extract_counterparty(t["description"])
            if cp:
                history.setdefault(cp, []).append(t)

        anomalies = []
        checked = 0
        for t in txns:
            cp = extract_counterparty(t["description"])
            if not cp:
                continue
            # history = same counterparty's other transactions
            hist = [h for h in history.get(cp, []) if h is not t]
            v = evaluate_transaction(t, hist)
            checked += 1
            if v.is_anomalous:
                d = v.to_dict()
                d["transaction_date"] = (
                    t["transaction_date"].isoformat()
                    if t["transaction_date"] else None
                )
                anomalies.append(d)
                if len(anomalies) >= limit:
                    break

        return {
            "anomalies": anomalies,
            "transactions_checked": checked,
            "rule": {
                "type": "amount_vs_counterparty_average",
                "multiplier": evaluate_transaction.__defaults__ and None
                or __import__("app.services.anomaly", fromlist=["DEFAULT_MULTIPLIER"]).DEFAULT_MULTIPLIER,
                "min_history": __import__("app.services.anomaly", fromlist=["DEFAULT_MIN_HISTORY"]).DEFAULT_MIN_HISTORY,
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
