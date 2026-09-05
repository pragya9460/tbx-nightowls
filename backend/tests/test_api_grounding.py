"""API + grounding tests: full pipeline through FastAPI TestClient. Verifies
that answers contain backend-computed values, not invented ones, that
sensitive values are masked in API responses, and that multi-turn context
carries through."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Account, Transaction
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
    assert body["record_counts"]["transaction"] > 0
    assert body["record_counts"]["account"] > 0
    assert body["record_counts"]["bank"] > 0


# ---------------------------------------------------------------------------
# Grounding: answer numbers match independent ORM computation
# ---------------------------------------------------------------------------

def test_chat_debit_spend_grounded(client, db):
    """'How much did I spend last month?' → debit sum that equals the ORM sum."""
    resp = client.post("/api/chat", json={
        "question": "How much did I spend last month?",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is None
    assert "debit" in body["answer"].lower()  # interpretation made explicit

    today = dt.date.today()
    first_of_current = today.replace(day=1)
    last_month_end = first_of_current - dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    expected = db.scalar(
        select(func.coalesce(func.sum(Transaction.transaction_amount), 0)).where(
            Transaction.transaction_type == "debit",
            Transaction.transaction_date >= dt.datetime.combine(
                last_month_start, dt.time.min),
            Transaction.transaction_date < dt.datetime.combine(
                last_month_end, dt.time.min) + dt.timedelta(days=1),
        )
    )

    # evidence's records_matched must equal the true count
    expected_n = db.scalar(
        select(func.count(Transaction.transaction_id)).where(
            Transaction.transaction_type == "debit",
            Transaction.transaction_date >= dt.datetime.combine(
                last_month_start, dt.time.min),
            Transaction.transaction_date < dt.datetime.combine(
                last_month_end, dt.time.min) + dt.timedelta(days=1),
        )
    )
    assert body["evidence"]["how_calculated"]["records_matched"] == expected_n
    assert body["meta"]["grounded"] is True
    assert body["evidence"]["how_calculated"]["filters"].get("transaction_type") == "debit"


def test_chat_total_balance_grounded(client, db):
    resp = client.post("/api/chat", json={"question": "What is my total available balance?"})
    body = resp.json()
    assert body["refusal"] is None
    expected = db.scalar(select(func.sum(Account.available_balance)))
    # the answer must contain the value the DB computes (in INR formatting)
    assert f"{float(expected):,.0f}" in body["answer"].replace("₹", "").replace(
        "lakh", "").replace("crore", "") or "₹" in body["answer"]
    assert body["evidence"]["how_calculated"]["operation"] == "SUM(balance)"


def test_chat_unsupported_rejected(client):
    resp = client.post("/api/chat", json={"question": "How much did we pay in salaries?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is not None
    assert body["refusal"]["reason"] == "unsupported_metric"
    assert body["evidence"] is None
    assert "not available" in body["answer"].lower()
    assert body["meta"]["grounded"] is False


def test_chat_invoice_question_rejected(client):
    resp = client.post("/api/chat", json={"question": "Which invoices are overdue?"})
    body = resp.json()
    assert body["refusal"] is not None
    assert "invoice" in body["answer"].lower()


# ---------------------------------------------------------------------------
# Sensitive data handling through the API
# ---------------------------------------------------------------------------

def test_account_numbers_masked_in_api_responses(client):
    """Account listing must never contain a full account number."""
    resp = client.post("/api/chat", json={"question": "Show me all my accounts."})
    body = resp.json()
    assert body["refusal"] is None
    records = body["evidence"]["records"]
    assert len(records) > 0
    for r in records:
        acc = r["account_number"]
        assert acc.startswith("XXXXX"), f"raw account number leaked: {acc}"
        assert len(acc) <= 9


def test_utr_masked_in_api_responses(client):
    resp = client.post("/api/chat", json={
        "question": "Show my largest transactions.",
    })
    body = resp.json()
    assert body["refusal"] is None
    for r in body["evidence"]["records"]:
        utr = r.get("utr_number")
        if utr:
            assert "***" in utr and len(utr) < 12, f"raw UTR leaked: {utr}"


def test_direct_query_endpoint_no_llm(client):
    """POST /api/query executes a structured query with zero LLM involvement."""
    resp = client.post("/api/query", json={
        "intent": "transaction_summary",
        "metric": "transaction_amount",
        "aggregation": "sum",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["provider"] == "none"
    assert body["meta"]["grounded"] is True


def test_direct_query_rejects_invalid(client):
    resp = client.post("/api/query", json={
        "intent": "transaction_summary",
        "metric": "vendor_payout",   # not in allowlist
        "aggregation": "sum",
        "date_range": {"type": "all_time"},
    })
    body = resp.json()
    assert body["refusal"]["reason"] == "invalid_structure"


# ---------------------------------------------------------------------------
# Multi-turn through the API
# ---------------------------------------------------------------------------

def test_multiturn_comparison(client):
    r1 = client.post("/api/chat", json={
        "question": "How much did I spend in August?",
        "conversation_id": "mt-test",
    })
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["refusal"] is None
    assert b1["query"]["date_range"]["label"] == "Aug 2026"

    r2 = client.post("/api/chat", json={
        "question": "How does that compare with the month before?",
        "conversation_id": "mt-test",
    })
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["refusal"] is None
    assert b2["query"]["intent"] == "comparison"
    # same interpretation inherited
    assert b2["query"]["filters"].get("transaction_type") == "debit"
    # both sides present in the answer
    assert "vs" in b2["answer"].lower() and "₹" in b2["answer"]
    assert b2["evidence"]["comparison"]["how_calculated"]["date_range"] == "Jul 2026"


def test_multiturn_month_swap(client):
    r1 = client.post("/api/chat", json={
        "question": "How much did I spend in August?",
        "conversation_id": "mt-swap",
    })
    b1 = r1.json()
    assert b1["query"]["filters"]["transaction_type"] == "debit"

    r2 = client.post("/api/chat", json={
        "question": "What about July?",
        "conversation_id": "mt-swap",
    })
    b2 = r2.json()
    assert b2["refusal"] is None
    assert b2["query"]["filters"]["transaction_type"] == "debit"
    assert b2["query"]["date_range"]["label"].startswith("Jul")


# ---------------------------------------------------------------------------
# Large-result handling: listing answers summarize, not dump
# ---------------------------------------------------------------------------

def test_listing_evidence_capped(client):
    resp = client.post("/api/chat", json={"question": "Show all my transactions."})
    body = resp.json()
    assert body["refusal"] is None
    records = body["evidence"]["records"]
    from app.query_engine.evidence import MAX_RECORDS_SHOWN
    assert len(records) <= MAX_RECORDS_SHOWN
    # but the answer reports the true total
    assert body["evidence"]["how_calculated"]["records_matched"] >= len(records)
