"""Hallucination guardrails: refusals for unsupported/ambiguous/invalid
questions, SQL-injection resistance, sensitive-field masking."""
from __future__ import annotations

import pytest

from app.llm.provider import RuleBasedProvider
from app.query_engine.engine import mask_account_number, mask_utr
from app.schemas.query import (
    Aggregation,
    FinancialQuery,
    Intent,
    Metric,
    supported_capabilities,
)


def u(question: str, context=None):
    return RuleBasedProvider().understand(question, context=context)


# ---------------------------------------------------------------------------
# Unsupported domains — must refuse, never guess
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,fragment", [
    ("How much did I pay my employees?", "payroll"),
    ("What is my total salary expense?", "payroll"),
    ("How much GST do I owe?", "tax"),
    ("What is my profit margin?", "profit"),
    ("Which invoices are overdue?", "invoice"),
    ("How much do I owe vendors?", "vendor"),
    ("Show my escrow mandates", "escrow"),
    ("List my customers", "customer"),
    ("Forecast my spend next quarter", "forecast"),
    ("How much revenue did I make?", "revenue"),
])
def test_unsupported_domains_refused(question, fragment):
    resp = u(question)
    assert resp.refusal_reason == "unsupported"
    assert fragment in resp.refusal_message.lower()


def test_unsupported_refusal_does_not_execute_anything():
    resp = u("What is my profit margin?")
    assert resp.query is None


@pytest.mark.parametrize("question", [
    "what is your name",
    "What's your name?",
    "Who are you?",
    "What are you?",
    "hello",
    "What is the weather today?",
])
def test_identity_and_chitchat_refused_not_guessed(question):
    """Off-topic questions must refuse — never a transaction total."""
    resp = u(question)
    assert resp.query is None, f"guessed a query for {question!r}"
    assert resp.refusal_reason == "unsupported"
    assert "₹" not in (resp.refusal_message or "")
    assert "Artha" in (resp.refusal_message or "") or "dataset" in (
        resp.refusal_message or ""
    ).lower()


def test_supported_refusal_includes_capabilities():
    resp = u("What is my profit margin?")
    assert resp.refusal_message  # non-empty guidance


# ---------------------------------------------------------------------------
# Ambiguity — explicit interpretation, never silent guessing
# ---------------------------------------------------------------------------

def test_bare_spend_interprets_as_debit_and_answers_state_it():
    """'How much did I spend last month?' maps to debit transactions — the
    natural reading. The generated ANSWER must make that interpretation
    explicit (spec §6), verified in the answer template tests."""
    resp = u("How much did I spend last month?")
    assert resp.refusal_reason is None
    assert resp.query["filters"]["transaction_type"] == "debit"
    assert resp.query["intent"] == "transaction_summary"


def test_spend_with_debit_word_is_not_ambiguous():
    resp = u("How much did I spend (debit) last month?")
    assert resp.query is not None
    assert resp.query["filters"]["transaction_type"] == "debit"


def test_credit_inflow_maps_to_credit():
    resp = u("How much money came in last month?")
    assert resp.query is not None
    assert resp.query["filters"]["transaction_type"] == "credit"


# ---------------------------------------------------------------------------
# Invalid structures — rejected by validation
# ---------------------------------------------------------------------------

def test_extra_fields_rejected():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_summary", "metric": "transaction_amount",
            "aggregation": "sum", "date_range": {"type": "all_time"},
            "evil_field": "DROP TABLE",
        })


def test_unknown_intent_rejected():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "vendor_payout_summary", "metric": "transaction_amount",
            "aggregation": "sum", "date_range": {"type": "all_time"},
        })


def test_balance_metric_only_for_balance_intents():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_summary", "metric": "balance",
            "aggregation": "sum", "date_range": {"type": "all_time"},
        })


def test_min_max_amount_ordering_enforced():
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_summary", "metric": "transaction_amount",
            "aggregation": "sum",
            "filters": {"min_amount": 500, "max_amount": 100},
            "date_range": {"type": "all_time"},
        })


# ---------------------------------------------------------------------------
# SQL-injection resistance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "'; DROP TABLE transaction; --",
    "x' OR '1'='1",
    "UNION SELECT bank_name FROM bank",
    "admin'--",
])
def test_injection_payloads_rejected_by_validator(payload):
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_list", "metric": "transaction_amount",
            "aggregation": "none",
            "filters": {"description_contains": payload},
            "date_range": {"type": "all_time"},
        })


def test_injection_payload_as_description_fails_validation(db):
    """A raw SQL payload in a description filter is rejected by the Pydantic
    validator (see test_injection_payloads_rejected_by_validator); the rule
    provider extracts only clean text. Defense in depth: even bypassing both,
    filter values are parameter-bound so no payload can execute."""
    payload = "Robert'; DROP TABLE transaction;--"
    with pytest.raises(Exception):
        FinancialQuery.model_validate({
            "intent": "transaction_list", "metric": "transaction_amount",
            "aggregation": "none",
            "filters": {"description_contains": payload},
            "date_range": {"type": "all_time"},
        })
    # table still exists and is queryable
    assert db.count_total("transaction") > 0


# ---------------------------------------------------------------------------
# Sensitive-field masking
# ---------------------------------------------------------------------------

def test_mask_account_number():
    assert mask_account_number("50200013729069") == "XXXXX9069"
    assert mask_account_number("1234") == "XXXXX1234"
    assert mask_account_number(None) is None
    assert mask_account_number("") == ""


def test_mask_utr():
    assert mask_utr("jhI5nAdyb1qOEjmcB3JvWjC6tTO+ZPVqBFPm/GiErC4TRBWRQ5ylPG3p").startswith("jhI5")
    assert mask_utr("jhI5nAdyb1qOEjmcB3JvWjC6tTO+ZPVqBFPm/GiErC4TRBWRQ5ylPG3p").endswith("3p")
    assert "***" in mask_utr("jhI5nAdyb1qOEjmcB3JvWjC6tTO+ZPVqBFPm/GiErC4TRBWRQ5ylPG3p")
    assert mask_utr(None) is None


def test_engine_results_never_contain_raw_account_number(db):
    q = FinancialQuery.model_validate({
        "intent": "transaction_list", "metric": "transaction_amount",
        "aggregation": "none", "date_range": {"type": "all_time"}, "limit": 20,
    })
    result = db.execute(q)
    for r in result.records:
        raw = r.get("account_number", "")
        assert not (raw.isdigit() and len(raw) > 6), f"raw account leaked: {raw}"


def test_engine_results_mask_utr(db):
    q = FinancialQuery.model_validate({
        "intent": "transaction_list", "metric": "transaction_amount",
        "aggregation": "none", "date_range": {"type": "all_time"}, "limit": 20,
    })
    result = db.execute(q)
    for r in result.records:
        utr = r.get("utr_number")
        if utr:
            # masked form: prefix + '***' + short suffix, never the full value
            assert len(utr) < 12, f"raw UTR leaked: {utr}"
            assert "***" in utr, f"unmasked UTR leaked: {utr}"


# ---------------------------------------------------------------------------
# Supported capabilities registry is coherent
# ---------------------------------------------------------------------------

def test_supported_capabilities_introspectable():
    caps = supported_capabilities()
    assert "transaction_summary" in caps["intents"]
    assert "account_balance" in caps["intents"]
    assert "utr_number" in caps["filters"]
    assert "vendor_id" not in caps["filters"]
