"""Query-state + confidence tests (Must-Have 5 states, confidence bonus) and
multi-turn filter refinement (Must-Have 7, Conversation 2)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm.provider import RuleBasedProvider
from app.main import app


@pytest.fixture()
def client():
    from app.api.routes import conversation_store

    conversation_store._conversations.clear()
    return TestClient(app)


CONTEXT_LISTING = {
    "last_intent": "transaction_list",
    "last_metric": "transaction_amount",
    "last_filters": {"transaction_type": "debit"},
    "last_date_range": {"type": "calendar_month", "start": "2026-08-01",
                        "end": "2026-08-31", "label": "Aug 2026"},
}


# ---------------------------------------------------------------------------
# Explicit query states (supported / empty_data / ambiguous / unsupported /
# invalid) — every chat response carries a machine-readable status.
# ---------------------------------------------------------------------------

def test_supported_status_and_high_confidence(client):
    body = client.post("/api/chat", json={
        "question": "How much did I spend last month?"}).json()
    assert body["status"] == "supported"
    assert body["confidence"] == "high"
    assert body["confidence_basis"]
    assert body["meta"]["grounded"] is True


def test_empty_data_status_on_zero_match(client):
    """A valid question with no matching data → empty_data + medium, a real
    zero — NOT a fabricated figure and NOT an error."""
    body = client.post("/api/chat", json={
        "question": "How much did I spend in January 2020?"}).json()
    assert body["refusal"] is None
    assert body["status"] == "empty_data"
    assert body["confidence"] == "medium"
    assert "no" in body["answer"].lower()


def test_ambiguous_status(client):
    body = client.post("/api/chat", json={
        "question": "How much moved last month?"}).json()
    assert body["status"] == "ambiguous"
    assert body["confidence"] == "none"
    assert body["evidence"] is None


def test_unsupported_status(client):
    body = client.post("/api/chat", json={
        "question": "How much did we spend on employee salaries?"}).json()
    assert body["status"] == "unsupported"
    assert body["confidence"] == "none"
    assert body["refusal"]["reason"] == "unsupported_metric"


def test_invalid_status_via_direct_query(client):
    body = client.post("/api/query", json={
        "intent": "transaction_summary", "metric": "transaction_amount",
        "aggregation": "sum", "date_range": {"type": "bogus_range"}}).json()
    assert body["status"] in ("invalid", "unsupported")
    assert body["refusal"] is not None


# ---------------------------------------------------------------------------
# Multi-turn filter refinement (spec Conversation 2)
# ---------------------------------------------------------------------------

def test_refinement_inherits_listing_filters():
    r = RuleBasedProvider().understand("Only those above ₹50,000.", CONTEXT_LISTING)
    assert r.query is not None
    assert r.query["intent"] == "transaction_list"
    assert r.query["filters"]["min_amount"] == 50000
    assert r.query["filters"]["transaction_type"] == "debit"  # inherited
    assert r.query["date_range"]["label"] == "Aug 2026"       # inherited


def test_refinement_type_switch():
    r = RuleBasedProvider().understand("Just the credits.", CONTEXT_LISTING)
    assert r.query["filters"]["transaction_type"] == "credit"
    assert r.query["date_range"]["label"] == "Aug 2026"


def test_refinement_requires_backreference():
    # a question with a NEW subject must not be treated as a refinement
    r = RuleBasedProvider().understand(
        "Which bank holds the most money?", CONTEXT_LISTING)
    assert r.query["intent"] == "bank_balance"


def test_refinement_without_context_is_normal_question():
    r = RuleBasedProvider().understand("Only those above ₹50,000.", {})
    # no prior listing → falls through to the standard amount-threshold path
    assert r.query is not None or r.refusal_reason is not None


def test_refinement_end_to_end_through_api(client):
    r1 = client.post("/api/chat", json={
        "question": "Show my largest debit transactions.",
        "conversation_id": "refine-1"}).json()
    assert r1["status"] == "supported"
    r2 = client.post("/api/chat", json={
        "question": "Only those above ₹50,000.",
        "conversation_id": "refine-1"}).json()
    assert r2["refusal"] is None, r2["answer"]
    assert r2["query"]["filters"].get("min_amount") == 50000
    assert r2["query"]["filters"].get("transaction_type") == "debit"
    assert r2["status"] == "supported"


# ---------------------------------------------------------------------------
# Evidence export (bonus): verbatim, masked rows
# ---------------------------------------------------------------------------

def test_export_csv_matches_displayed_records(client):
    chat = client.post("/api/chat", json={
        "question": "Show my largest transactions."}).json()
    records = chat["evidence"]["records"]
    resp = client.post("/api/export/evidence", json={
        "rows": records, "format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("transaction_id")
    # exactly the displayed rows (+ header)
    assert len(lines) == len(records) + 1
    # masked account numbers in the export too
    if "account_number" in lines[0]:
        col = lines[0].split(",").index("account_number")
        for line in lines[1:]:
            assert "XXXXX" in line.split(",")[col]


def test_export_rejects_empty(client):
    resp = client.post("/api/export/evidence", json={"rows": []})
    assert resp.status_code == 422
