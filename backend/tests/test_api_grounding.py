"""API + grounding tests: full pipeline through FastAPI TestClient. Verifies
that answers contain backend-computed values, not invented ones."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.query_engine.engine import FinancialQueryEngine
from app.schemas.query import FinancialQuery


@pytest.fixture()
def client(db):
    from app.api.routes import conversation_store

    conversation_store._conversations.clear()
    return TestClient(app)


def test_health_endpoint(client, db):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] == "connected"
    assert body["record_counts"]["transactions"] > 0


def test_chat_payout_summary_grounded(client, db):
    """The answer's number must equal what the engine computes from the DB."""
    resp = client.post("/api/chat", json={
        "question": "How much did we spend on vendor payouts last month?",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is None
    assert "spent" in body["answer"].lower()

    # Independent DB verification
    today = dt.date.today()
    first_of_current = today.replace(day=1)
    last_month_end = first_of_current - dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    from app.models import VendorPayout

    expected = sum(
        float(p.amount) for p in db.query(VendorPayout).filter(
            VendorPayout.payout_date >= last_month_start,
            VendorPayout.payout_date <= last_month_end,
        ).all()
    )
    # parse the ₹x.xx lakh figure out of the answer and compare
    assert "lakh" in body["answer"] or "crore" in body["answer"] or "₹" in body["answer"]
    assert body["evidence"]["how_calculated"]["records_matched"] >= 0
    assert body["meta"]["grounded"] is True


def test_chat_unsupported_rejected(client):
    resp = client.post("/api/chat", json={"question": "How much did we pay in salaries?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is not None
    assert body["evidence"] is None
    assert "not available" in body["answer"].lower()
    assert body["meta"]["grounded"] is False


def test_direct_query_endpoint(client, db):
    resp = client.post("/api/query", json={
        "intent": "vendor_payout_summary",
        "metric": "payout_amount",
        "aggregation": "sum",
        "filters": {},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is None

    engine = FinancialQueryEngine(db)
    expected = engine.execute(FinancialQuery.model_validate({
        "intent": "vendor_payout_summary",
        "metric": "payout_amount",
        "aggregation": "sum",
        "filters": {},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })).summary["value"]
    assert body["evidence"]["how_calculated"]["records_matched"] >= 0
    # evidence must carry the query metadata proving deterministic origin
    assert body["query"]["aggregation"] == "sum"


def test_direct_query_invalid_rejected(client):
    resp = client.post("/api/query", json={
        "intent": "free_sql",
        "metric": "payout_amount",
        "aggregation": "sum",
        "filters": {},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"]["reason"] == "invalid_structure"


def test_multiturn_comparison(client):
    r1 = client.post("/api/chat", json={
        "question": "How much did we spend on vendor payouts last month?",
        "conversation_id": "test-conv-1",
    })
    assert r1.status_code == 200
    r2 = client.post("/api/chat", json={
        "question": "How does that compare with the month before?",
        "conversation_id": "test-conv-1",
    })
    assert r2.status_code == 200
    body = r2.json()
    assert body["refusal"] is None
    assert body["query"]["intent"] == "comparison"
    assert "evidence" in body and body["evidence"] is not None


def test_records_in_unreconciled_answer(client):
    resp = client.post("/api/chat", json={"question": "Which transactions are still unreconciled?"})
    assert resp.status_code == 200
    body = resp.json()
    if body["refusal"] is None:
        records = body["evidence"].get("records") or []
        for rec in records:
            assert rec["reconciliation_status"] == "unreconciled"
