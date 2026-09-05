"""Bank-count intent: question → structured query → engine → answer.

Grounded path only — the count comes from a compiled SELECT over the bank
table, computed against independently derived expected values.
"""
from __future__ import annotations

from app.llm.provider import RuleBasedProvider
from app.schemas.query import FinancialQuery
from app.services.answers import generate_answer


def u(question, context=None):
    return RuleBasedProvider().understand(question, context=context)


def q(question, context=None):
    r = u(question, context)
    assert r.query is not None, f"expected a query, got refusal: {r.refusal_reason}"
    return r.query


# ----- provider understanding -------------------------------------------------

def test_how_many_banks_maps_to_bank_count():
    query = q("How many total number of banks are there")
    assert query["intent"] == "bank_count"
    assert query["aggregation"] == "count"


def test_how_many_banks_do_we_have():
    query = q("How many banks do we have?")
    assert query["intent"] == "bank_count"


def test_count_of_banks_maps_to_bank_count():
    query = q("What is the count of banks in the dataset?")
    assert query["intent"] == "bank_count"


def test_bank_count_does_not_hijack_how_many_accounts():
    query = q("How many accounts do I have per bank?")
    assert query["intent"] == "bank_account_count"


def test_bank_count_does_not_hijack_bank_accounts():
    """'how many bank accounts' is about ACCOUNTS — must not route to
    bank_count (regression: it answered '10 banks')."""
    query = q("How many bank accounts are associated?")
    assert query["intent"] == "bank_account_count"


def test_how_many_bank_accounts_total_maps_to_account_count():
    query = q("How many bank accounts do I have?")
    assert query["intent"] == "bank_account_count"


def test_bank_count_does_not_hijack_bank_transactions():
    query = q("How many transactions did I make with HDFC last month?")
    assert query["intent"] == "transaction_summary"


def test_bank_count_validates_against_schema():
    raw = q("How many total number of banks are there")
    # must pass the closed schema (extra=forbid) untouched
    fq = FinancialQuery.model_validate(raw)
    assert fq.intent.value == "bank_count"


# ----- engine + answer (integration, real MySQL test DB) -----------------------

def test_bank_count_executes_and_matches_expected(mysql_engine):
    raw = q("How many total number of banks are there")
    fq = FinancialQuery.model_validate(raw)
    result = mysql_engine.execute(fq)

    expected = mysql_engine.count_total("bank")
    assert result.summary["value"] == expected
    assert result.summary["value"] == 10  # deterministic seed: 10 banks


def test_bank_count_answer_names_the_count(mysql_engine):
    raw = q("How many total number of banks are there")
    fq = FinancialQuery.model_validate(raw)
    result = mysql_engine.execute(fq)
    answer = generate_answer(fq, result)
    assert "10 banks" in answer


def test_bank_count_zero_banks_edge(mysql_engine):
    from app.schemas.query import QueryFilters

    raw = q("How many total number of banks are there")
    fq = FinancialQuery.model_validate(raw)
    fq = fq.model_copy(update={"filters": QueryFilters(bank_code="NOPE")})
    result = mysql_engine.execute(fq)
    assert result.summary["value"] == 0
    answer = generate_answer(fq, result)
    assert "No banks found" in answer
