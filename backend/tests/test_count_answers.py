"""Count questions: routing scope + answer rendering.

Regression coverage for two user-reported bugs:
1. "How many transactions belong to HDFC?" (no period) defaulted to last
   month — scope questions now default to all_time.
2. Count answers rendered "₹58" — counts are not money.
"""
from __future__ import annotations

import pytest

from app.llm.provider import RuleBasedProvider
from app.schemas.query import FinancialQuery, Metric
from app.services.answers import generate_answer


def u(question, context=None):
    return RuleBasedProvider().understand(question, context=context)


def q(question, context=None):
    r = u(question, context)
    assert r.query is not None, f"expected a query, got refusal: {r.refusal_reason}"
    return r.query


# ----- routing: scope questions default to all time ----------------------------

def test_count_without_period_is_all_time():
    query = q("How many transaction belong to hdfc account??")
    assert query["metric"] == "transaction_count"
    assert query["filters"].get("bank_code") == "HDFC"
    assert query["date_range"]["type"] == "all_time"


def test_count_with_named_month_stays_scoped():
    query = q("How many transactions did I make in July?")
    assert query["metric"] == "transaction_count"
    assert query["date_range"]["type"] == "calendar_month"


def test_count_last_month_stays_last_month():
    query = q("How many transactions happened last month?")
    assert query["date_range"]["type"] == "calendar_month"


def test_count_with_year_stays_scoped():
    query = q("How many transactions in 2026?")
    assert query["date_range"]["type"] in ("calendar_month", "custom", "this_year")


# ----- money keeps the last-month default ---------------------------------------

def test_spend_without_period_keeps_last_month_default():
    query = q("How much did I spend?")
    assert query["metric"] == "transaction_amount"
    assert query["date_range"]["type"] == "calendar_month"


# ----- answer rendering: counts are not money ------------------------------------

def test_count_answer_has_no_rupee_symbol(mysql_engine):
    raw = q("How many transaction belong to hdfc account??")
    fq = FinancialQuery.model_validate(raw)
    result = mysql_engine.execute(fq)
    answer = generate_answer(fq, result)
    assert "₹" not in answer, f"count answer must not contain ₹: {answer}"
    assert str(result.summary["value"]) in answer or f"{result.summary['value']:,}" in answer


def test_count_answer_states_the_count(mysql_engine):
    raw = q("How many transaction belong to hdfc account??")
    fq = FinancialQuery.model_validate(raw)
    result = mysql_engine.execute(fq)

    # independent expected value from the same test DB
    expected = 0
    with mysql_engine._con.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM `transaction` t "
            "JOIN account a ON t.account_id=a.account_id "
            "JOIN bank b ON a.bank_code=b.bank_code WHERE b.bank_code='HDFC'"
        )
        expected = int(cur.fetchone()["cnt"])

    assert result.summary["value"] == expected
    answer = generate_answer(fq, result)
    assert "transactions" in answer


def test_amount_answer_still_formats_as_money(mysql_engine):
    raw = q("How much did I spend last month?")
    fq = FinancialQuery.model_validate(raw)
    result = mysql_engine.execute(fq)
    answer = generate_answer(fq, result)
    assert "₹" in answer
