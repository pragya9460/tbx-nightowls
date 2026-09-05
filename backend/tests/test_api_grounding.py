"""API + grounding tests: full pipeline through FastAPI TestClient. Verifies
that answers contain backend-computed values, not invented ones, that
sensitive values are masked in API responses, and that multi-turn context
carries through.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.query import FinancialQuery, today as app_today


@pytest.fixture()
def client(duckdb_file):
    from app.api.routes import conversation_store

    conversation_store._conversations.clear()
    return TestClient(app)


def test_health_endpoint(client, duckdb_file):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] == "connected"
    assert body["record_counts"]["transaction"] > 0
    assert body["record_counts"]["account"] > 0
    assert body["record_counts"]["bank"] > 0


def test_chat_debit_spend_grounded(client, duck_engine):
    """'How much did I spend last month?' → debit sum that equals DuckDB."""
    resp = client.post("/api/chat", json={
        "question": "How much did I spend last month?",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["refusal"] is None
    assert "debit" in body["answer"].lower() or "spent" in body["answer"].lower()
    assert body["meta"]["grounded"] is True
    assert body["meta"].get("backend") == "duckdb"
    assert body["evidence"]["how_calculated"]["filters"].get("transaction_type") == "debit"
    assert body["evidence"]["how_calculated"]["records_matched"] >= 0
    assert "sql" in body["evidence"]["how_calculated"]

    today = app_today()
    first_of_current = today.replace(day=1)
    last_month_end = first_of_current - dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    expected_n = duck_engine._con.execute(
        """
        SELECT COUNT(DISTINCT t.transaction_id)
        FROM "transaction" t
        WHERE t.transaction_type = 'debit'
          AND CAST(t.transaction_date AS DATE) >= ?
          AND CAST(t.transaction_date AS DATE) <= ?
        """,
        [last_month_start.isoformat(), last_month_end.isoformat()],
    ).fetchone()[0]
    assert body["evidence"]["how_calculated"]["records_matched"] == expected_n


def test_chat_total_balance_grounded(client, duck_engine):
    resp = client.post("/api/chat", json={"question": "What is my total available balance?"})
    body = resp.json()
    assert body["refusal"] is None
    expected = duck_engine._con.execute(
        "SELECT SUM(available_balance) FROM account"
    ).fetchone()[0]
    assert "₹" in body["answer"]
    assert body["evidence"]["how_calculated"]["operation"] == "SUM(balance)"
    assert body["evidence"]["summary"]["value"] == pytest.approx(float(expected), rel=1e-6)


def test_chat_unsupported_rejected(client, duckdb_file):
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


def test_chat_identity_question_refused(client):
    resp = client.post("/api/chat", json={"question": "what is your name"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["refusal"] is not None
    assert body["evidence"] is None
    assert body["query"] is None
    assert body["meta"]["grounded"] is False
    assert "₹" not in body["answer"]
    assert "transacted" not in body["answer"].lower()


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


def test_direct_query_endpoint_no_llm(client, duck_engine):
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
    assert body["refusal"] is None
    assert body["meta"]["provider"] == "none"
    assert body["meta"]["grounded"] is True
    assert body["meta"].get("backend") == "duckdb"

    expected = duck_engine.execute(FinancialQuery.model_validate({
        "intent": "transaction_summary",
        "metric": "transaction_amount",
        "aggregation": "sum",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "custom", "start": "2026-08-01", "end": "2026-08-31"},
    })).summary["value"]
    assert body["evidence"]["summary"]["value"] == pytest.approx(expected, rel=1e-6)


def test_direct_query_rejects_invalid(client):
    resp = client.post("/api/query", json={
        "intent": "transaction_summary",
        "metric": "vendor_payout",   # not in allowlist
        "aggregation": "sum",
        "date_range": {"type": "all_time"},
    })
    body = resp.json()
    assert body["refusal"]["reason"] == "invalid_structure"


def test_multiturn_comparison(client, duckdb_file):
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
    assert b2["query"]["filters"].get("transaction_type") == "debit"
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


def test_listing_evidence_capped(client):
    resp = client.post("/api/chat", json={"question": "Show all my transactions."})
    body = resp.json()
    assert body["refusal"] is None
    records = body["evidence"]["records"]
    from app.query_engine.evidence import MAX_RECORDS_SHOWN
    assert len(records) <= MAX_RECORDS_SHOWN
    assert body["evidence"]["how_calculated"]["records_matched"] >= len(records)
