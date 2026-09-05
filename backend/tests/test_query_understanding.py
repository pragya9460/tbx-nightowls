"""Rule-based provider understanding tests: question → structured query."""
from __future__ import annotations

import pytest

from app.llm.provider import RuleBasedProvider


def u(question, context=None):
    return RuleBasedProvider().understand(question, context=context)


def q(question, context=None):
    r = u(question, context)
    assert r.query is not None, f"expected a query, got refusal: {r.refusal_reason}"
    return r.query


# ---------------------------------------------------------------------------
# Account intelligence
# ---------------------------------------------------------------------------

def test_available_balance_defaults_to_total():
    query = q("What is my available balance?")
    assert query["intent"] == "account_balance"
    assert query["metric"] == "balance"
    assert query["aggregation"] == "sum"


def test_total_balance_across_accounts():
    query = q("What is the total available balance across all accounts?")
    assert query["intent"] == "account_balance"


def test_highest_balance_account():
    query = q("Which account has the highest balance?")
    assert query["intent"] == "account_balance"
    assert query["group_by"] == ["account"]
    assert query["limit"] == 5


def test_bank_holds_most_money():
    query = q("Which bank holds the most money?")
    assert query["intent"] == "bank_balance"
    assert query["group_by"] == ["bank"]


def test_show_all_accounts():
    query = q("Show me all my accounts.")
    assert query["intent"] == "account_list"


def test_balance_in_hdfc():
    query = q("What is the balance of my HDFC account?")
    assert query["intent"] == "account_balance"
    assert query["filters"].get("bank_code") == "HDFC"


def test_balance_bank_alias_sbi():
    query = q("How much money do I have in SBI?")
    assert query["filters"].get("bank_code") == "SBIN"


def test_accounts_per_bank():
    query = q("How many accounts do I have with each bank?")
    assert query["intent"] == "bank_account_count"


# ---------------------------------------------------------------------------
# Transaction intelligence
# ---------------------------------------------------------------------------

def test_spend_last_month_is_debit():
    query = q("How much did I spend last month?")
    assert query["intent"] == "transaction_summary"
    assert query["filters"]["transaction_type"] == "debit"
    assert query["date_range"]["type"] == "calendar_month"


def test_money_came_in_is_credit():
    query = q("How much money came in last month?")
    assert query["filters"]["transaction_type"] == "credit"


def test_spend_in_june_named_month():
    query = q("How much did I spend in June?")
    assert query["date_range"]["type"] == "calendar_month"
    assert query["date_range"]["label"] == "Jun 2026" or "Jun" in (query["date_range"].get("label") or "")


def test_named_month_with_year():
    query = q("How much did I spend in August 2026?")
    dr = query["date_range"]
    assert dr["start"].startswith("2026-08-01")


def test_this_month_range():
    query = q("How much did I spend this month?")
    assert query["date_range"]["type"] == "this_month"


def test_last_week_range():
    query = q("How much did I spend last week?")
    assert query["date_range"]["type"] == "last_week"


def test_last_7_days():
    query = q("How much did I spend in the last 7 days?")
    assert query["date_range"]["type"] == "last_n_days"
    assert query["date_range"]["n_days"] == 7


def test_last_30_days():
    query = q("How much did I spend in the last 30 days?")
    assert query["date_range"]["n_days"] == 30


def test_largest_transactions():
    query = q("Show my largest transactions.")
    assert query["intent"] == "transaction_list"
    assert query["limit"] == 10


def test_biggest_debit_transactions():
    query = q("What were my biggest debit transactions?")
    assert query["intent"] == "transaction_list"
    assert query["filters"]["transaction_type"] == "debit"


def test_transactions_above_amount():
    query = q("Show transactions above ₹50,000.")
    assert query["intent"] == "transaction_list"
    assert query["filters"]["min_amount"] == 50000


def test_spend_at_named_counterparty():
    query = q("What did I spend at Selection Electronics last month?")
    assert query["filters"]["description_contains"] == "SELECTION ELECTRONICS"


def test_transactions_containing_reliance():
    query = q("Show all transactions containing Reliance.")
    assert query["filters"]["description_contains"] == "RELIANCE"


def test_which_month_highest_debit():
    query = q("Which month had the highest debit amount?")
    assert query["intent"] == "monthly_trend"
    assert query["group_by"] == ["month"]
    assert query["filters"]["transaction_type"] == "debit"


def test_how_many_transactions_last_month():
    query = q("How many transactions happened last month?")
    assert query["metric"] == "transaction_count"
    assert query["aggregation"] == "count"


def test_top_descriptions_by_spend():
    query = q("What are my top transaction descriptions by spend?")
    assert query["intent"] == "top_descriptions"


def test_transactions_from_sbi_accounts():
    query = q("Show all transactions from my SBI accounts.")
    assert query["filters"]["bank_code"] == "SBIN"
    assert query["intent"] == "transaction_list"


# ---------------------------------------------------------------------------
# Reference search — the two reference columns are NOT interchangeable
# ---------------------------------------------------------------------------

def test_bare_reference_hits_transaction_reference_id():
    query = q("Find transaction reference 1715499972.")
    assert query["intent"] == "reference_lookup"
    assert query["filters"].get("reference_id") == "1715499972"
    assert "utr_number" not in query["filters"]


def test_reference_with_s_prefix():
    query = q("Show transaction with reference S5314253.")
    assert query["filters"].get("reference_id") == "S5314253"


def test_explicit_utr_hits_utr_column():
    query = q("Find UTR jhI5nAdyb1qOEjmcB3JvWjC6tTO")
    assert query["filters"].get("utr_number") == "jhI5nAdyb1qOEjmcB3JvWjC6tTO"
    assert "reference_id" not in query["filters"]


def test_utr_without_value_asks_for_it():
    r = u("Find UTR")
    assert r.refusal_reason == "ambiguous"


# ---------------------------------------------------------------------------
# Multi-turn context (spec §7)
# ---------------------------------------------------------------------------

def test_what_about_july_reuses_metric_and_type():
    first = q("How much did I spend in August?", )
    assert first["filters"]["transaction_type"] == "debit"
    context = {
        "last_intent": first["intent"],
        "last_metric": first["metric"],
        "last_filters": first["filters"],
        "last_date_range": first["date_range"],
    }
    follow = q("What about July?", context)
    assert follow["filters"]["transaction_type"] == "debit"
    assert follow["date_range"]["label"].startswith("Jul")


def test_which_bank_contributed_most_reuses_context():
    context = {
        "last_intent": "transaction_summary",
        "last_metric": "transaction_amount",
        "last_filters": {"transaction_type": "debit",
                         "date_range": {"type": "calendar_month"}},
        "last_date_range": {"type": "calendar_month", "start": "2026-08-01",
                            "end": "2026-08-31", "label": "Aug 2026"},
    }
    follow = q("Which bank contributed the most?", context)
    # context reuse: keeps debit type from the previous turn
    assert follow["filters"].get("transaction_type") == "debit"


def test_comparison_followup():
    context = {
        "last_intent": "transaction_summary",
        "last_metric": "transaction_amount",
        "last_filters": {"transaction_type": "debit"},
        "last_date_range": {"type": "calendar_month", "start": "2026-08-01",
                            "end": "2026-08-31", "label": "Aug 2026"},
    }
    follow = q("How does that compare with the month before?", context)
    assert follow["intent"] == "comparison"
    assert follow["comparison"]["against"] == "previous_period"
    assert follow["filters"]["transaction_type"] == "debit"
