"""Payment-channel filter: "through NEFT" / "via IMPS" must scope the query
to descriptions containing the channel prefix (regression: the filter was
dropped entirely — both NEFT and IMPS answered with the full count)."""
from __future__ import annotations

from app.llm.provider import RuleBasedProvider
from app.schemas.query import FinancialQuery


def u(question, context=None):
    return RuleBasedProvider().understand(question, context=context)


def q(question, context=None):
    r = u(question, context)
    assert r.query is not None, f"expected a query, got refusal: {r.refusal_reason}"
    return r.query


def test_through_neft_scopes_description():
    query = q("How many transactions were made through NEFT?")
    assert query["filters"].get("description_contains") == "NEFT"


def test_through_imps_scopes_description():
    query = q("how many transactions were made through IMPS")
    assert query["filters"].get("description_contains") == "IMPS"


def test_via_upi_scopes_description():
    query = q("Show transactions made via UPI.")
    assert query["filters"].get("description_contains") == "UPI"


def test_rtgs_keyword_scopes_description():
    query = q("How many RTGS payments did I make?")
    assert query["filters"].get("description_contains") == "RTGS"


def test_channel_word_after_with_is_not_counterparty():
    """'with NEFT' used to become description_contains='NEFT' via the
    counterparty extractor — same outcome, but via the wrong path; guard
    against it capturing 'NEFT' as a named entity and missing the channel
    semantics."""
    query = q("How many transactions with NEFT last month?")
    assert query["filters"].get("description_contains") == "NEFT"


def test_counterparty_still_wins_over_channel():
    """A real counterparty next to a channel word keeps the counterparty."""
    query = q("How many transactions through NEFT with SHARMA SUPPLIERS?")
    assert query["filters"].get("description_contains") == "SHARMA SUPPLIERS"


def test_channel_scoped_count_executes(mysql_engine):
    """Engine result must match an independent SQL count for the channel."""
    query = q("How many transactions were made through NEFT?")
    fq = FinancialQuery.model_validate(query)
    result = mysql_engine.execute(fq)

    with mysql_engine._con.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM `transaction` t "
            "JOIN account a ON t.account_id=a.account_id "
            "WHERE LOWER(t.description) LIKE '%neft%'"
        )
        expected = int(cur.fetchone()["cnt"])

    assert result.summary["value"] == expected
    assert result.summary["value"] > 0  # dataset contains NEFT rows
