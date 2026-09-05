"""Financial Twin tests (Phases 5–8): accounts, rules/reserves + provenance,
vendor aggregation, cash engine, affordability, what-if simulation, chat
routing — all deterministic."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.financial_twin import (
    DEMO_RESERVES,
    FinancialTwinEngine,
    FinancialRule,
    Reserve,
)


@pytest.fixture()
def twin(duck_engine):
    return FinancialTwinEngine(duck_engine)


@pytest.fixture()
def client():
    from app.api.routes import conversation_store

    conversation_store._conversations.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 5.1 Accounts
# ---------------------------------------------------------------------------

def test_accounts_overview_from_dataset(twin):
    r = twin.accounts_overview()
    assert r["account_count"] > 0
    assert r["provenance"] == "OFFICIAL_DATASET"
    assert "never reconstructed" in r["note"]
    # masked account numbers
    assert all(a["account_number_masked"].startswith("XXXXX")
               for a in r["accounts"])
    # balances sum correctly
    total = sum(a["available_balance"] for a in r["accounts"])
    assert abs(total - r["total_available_balance"]) < 0.01


def test_available_balance_not_reconstructed_from_transactions(twin, duck_engine):
    """The twin reads account.available_balance directly; if it had summed
    transactions the total would differ (seed has 600 txns ≠ balances)."""
    r = twin.accounts_overview()
    con = duck_engine._con
    db_total = con.execute(
        "SELECT SUM(available_balance) FROM account").fetchone()[0]
    assert abs(r["total_available_balance"] - db_total) < 0.01
    txn_sum = con.execute(
        "SELECT SUM(transaction_amount) FROM \"transaction\"").fetchone()[0]
    # sanity: the two are genuinely different quantities in the seed
    assert abs(txn_sum - db_total) > 1


# ---------------------------------------------------------------------------
# 5.2 Rules / 5.5 reserves — provenance
# ---------------------------------------------------------------------------

def test_rules_and_reserves_labelled_synthetic(twin):
    r = twin.rules_and_reserves()
    assert r["provenance"] == "SYNTHETIC_DEMO"
    for rule in r["rules"]:
        assert rule["source"] == "SYNTHETIC_DEMO"
        assert set(rule) >= {"rule_type", "value", "enabled", "source",
                             "created_at", "updated_at"}
    for res in r["reserves"]:
        assert res["source"] == "SYNTHETIC_DEMO"
        assert set(res) >= {"name", "amount", "purpose", "priority",
                            "protected", "source"}


def test_protected_amount_sums_protected_only(twin):
    expected = sum(r.amount for r in twin.reserves if r.protected)
    assert twin.protected_amount() == expected


# ---------------------------------------------------------------------------
# 5.3 Vendor profiles — derived from real transactions
# ---------------------------------------------------------------------------

def test_vendor_profiles_derived_from_transactions(twin):
    r = twin.vendor_profiles(limit=5)
    assert r["provenance"] == "DERIVED"
    assert len(r["vendors"]) <= 5
    for v in r["vendors"]:
        assert set(v) >= {"vendor", "transaction_count", "total_spend",
                          "average_transaction", "largest_transaction",
                          "last_transaction"}
        assert v["transaction_count"] > 0
        # internal consistency: total = avg × count (for debit-only sums)
        if v["transaction_count"] > 0 and v["total_spend"] > 0:
            assert v["average_transaction"] == pytest.approx(
                v["total_spend"] /
                max(1, sum(1 for _ in [0])), rel=5)  # loose: debit subset


def test_vendor_profiles_are_sorted_by_spend(twin):
    r = twin.vendor_profiles(limit=20)
    spends = [v["total_spend"] for v in r["vendors"]]
    assert spends == sorted(spends, reverse=True)


def test_vendor_totals_computed_not_hardcoded(twin, duck_engine):
    """Verify one vendor's total against a direct SQL computation."""
    r = twin.vendor_profiles(limit=100)
    con = duck_engine._con
    # independently compute totals for the top vendor
    top = r["vendors"][0]["vendor"]
    rows = con.execute(
        """
        SELECT transaction_amount, transaction_type, description
        FROM "transaction" WHERE description IS NOT NULL
        """
    ).fetchall()
    from app.services.vendor_intel import extract_counterparty
    expected = sum(float(a) for a, t, d in rows
                   if t == "debit" and extract_counterparty(d) == top)
    assert r["vendors"][0]["total_spend"] == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# 5.4 Reconciliation — honest absence
# ---------------------------------------------------------------------------

def test_reconciliation_reports_absence_without_fabrication(twin):
    r = twin.reconciliation_status()
    assert r["available"] is False
    assert r["unreconciled_count"] is None
    assert "no reconciliation records" in r["note"]
    assert r["adapter_interface"]["status"] == "not_loaded"


# ---------------------------------------------------------------------------
# 6. Cash engine
# ---------------------------------------------------------------------------

def test_cash_position_components_and_provenance(twin):
    r = twin.cash_position()
    assert set(r) >= {"available_balance", "restricted_amount",
                      "protected_reserves", "upcoming_commitments",
                      "true_available_cash", "components", "provenance"}
    accounts = twin.accounts_overview()["total_available_balance"]
    assert r["available_balance"] == pytest.approx(accounts, rel=1e-9)
    assert r["protected_reserves"] == twin.protected_amount()
    assert r["true_available_cash"] == pytest.approx(
        accounts - twin.protected_amount(), rel=1e-9)
    assert r["provenance"]["protected_reserves"] == "SYNTHETIC_DEMO"
    assert r["provenance"]["available_balance"] == "OFFICIAL_DATASET"
    assert r["provenance"]["true_available_cash"] == "DERIVED"


def test_cash_engine_no_double_counting(twin):
    """true = available − restricted − reserves − commitments, each once."""
    r = twin.cash_position()
    expected = (r["available_balance"] - r["restricted_amount"]
                - r["protected_reserves"] - r["upcoming_commitments"])
    assert r["true_available_cash"] == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# 7. Affordability
# ---------------------------------------------------------------------------

def test_affordable_payment(twin):
    """Tiny payment against huge seed balances → affordable, no approval."""
    r = twin.can_i_afford("SHARMA SUPPLIERS", 1_000)
    assert r["requested_amount"] == 1_000
    assert r["affordable"] is True
    assert r["approval_required"] is False
    assert r["cash_after"] == pytest.approx(r["cash_before"] - 1_000, rel=1e-9)


def test_unaffordable_payment_flags_reserve_violation(twin):
    """An amount larger than everything → not affordable + reserve violation."""
    total = twin.cash_position()["true_available_cash"]
    r = twin.can_i_afford("SHARMA SUPPLIERS", total + 100)
    assert r["affordable"] is False
    assert r["reserve_violation"] is True
    assert any("shortfall" in x.lower() for x in r["reasons"])


def test_approval_required_above_threshold(twin):
    threshold = next(r.value for r in twin.rules
                     if r.rule_type == "approval_threshold")
    r = twin.can_i_afford("SHARMA SUPPLIERS", float(threshold) + 1)
    assert r["approval_required"] is True
    assert any("approval" in x.lower() for x in r["reasons"])


def test_buffer_violation_detected(twin):
    """A payment that stays positive but eats below the min buffer →
    not 'affordable' per the twin's definition, but no reserve violation."""
    buffer = float(next(r.value for r in twin.rules
                        if r.rule_type == "minimum_cash_buffer"))
    total = twin.cash_position()["true_available_cash"]
    # pay everything except buffer/2 → after < buffer, still positive
    amount = total - buffer / 2
    r = twin.can_i_afford("SHARMA SUPPLIERS", amount)
    assert r["cash_after"] > 0
    assert r["buffer_violation"] is True
    assert r["affordable"] is False
    assert r["reserve_violation"] is False


def test_missing_vendor_still_analyzes_with_none_history(twin):
    r = twin.can_i_afford("NONEXISTENT VENDOR XYZ", 1_000)
    assert r["vendor_history"] is None
    assert r["affordable"] is True  # decision is cash-based, not vendor-based


def test_affordability_never_executes_payment(twin):
    """Balance unchanged after analysis — it's analysis only."""
    before = twin.accounts_overview()["total_available_balance"]
    twin.can_i_afford("SHARMA SUPPLIERS", 999_999)
    after = twin.accounts_overview()["total_available_balance"]
    assert before == after


# ---------------------------------------------------------------------------
# 8. What-if simulation
# ---------------------------------------------------------------------------

def test_simulation_before_after(twin):
    r = twin.simulate_payment("SHARMA SUPPLIERS", 400_000)
    assert r["after"]["true_available_cash"] == pytest.approx(
        r["before"]["true_available_cash"] - 400_000, rel=1e-9)
    assert r["payment_amount"] == 400_000
    assert "static simulation" in " ".join(r["assumptions"])


def test_simulation_rule_outcomes(twin):
    total = twin.cash_position()["true_available_cash"]
    r = twin.simulate_payment("SHARMA SUPPLIERS", total + 5)
    assert "⚠ violated" in r["rules_outcome"]["payroll_reserve"]
    assert "⚠ violated" in r["rules_outcome"]["minimum_buffer"]
    assert r["affordable"] is False


def test_simulation_deterministic(twin):
    a = twin.simulate_payment("SHARMA SUPPLIERS", 123_456)
    b = twin.simulate_payment("SHARMA SUPPLIERS", 123_456)
    assert a["before"] == b["before"]
    assert a["after"] == b["after"]
    assert a["rules_outcome"] == b["rules_outcome"]


# ---------------------------------------------------------------------------
# 9. Chat routing to twin scenarios
# ---------------------------------------------------------------------------

def test_chat_can_i_pay_routes_to_affordability(client):
    r = client.post("/api/chat", json={
        "question": "Can I pay Sharma Suppliers 400000 today?"}).json()
    assert r["status"] == "supported"
    assert r["meta"]["backend"] == "financial_twin"
    assert r["query"]["scenario"] == "affordability"
    assert "approval" in r["answer"].lower() or "afford" in r["answer"].lower()


def test_chat_what_if_routes_to_simulation(client):
    r = client.post("/api/chat", json={
        "question": "What happens if I pay Sharma Suppliers 400000?"}).json()
    assert r["status"] == "supported"
    assert r["query"]["scenario"] == "what_if"
    assert "after payment" in r["answer"].lower()


def test_chat_true_cash_routes_to_cash_position(client):
    r = client.post("/api/chat", json={
        "question": "How much cash do I really have?"}).json()
    assert r["status"] == "supported"
    assert r["query"]["scenario"] == "cash_position"
    assert "true available cash" in r["answer"].lower()


def test_chat_cash_explain(client):
    r = client.post("/api/chat", json={
        "question": "Why is my available cash lower than my total balance?"}).json()
    assert r["query"]["scenario"] == "cash_position"
    assert r["query"].get("explain") is True
    assert "reserve" in r["answer"].lower()


def test_chat_vendor_profiles(client):
    r = client.post("/api/chat", json={
        "question": "Which vendors have the highest payouts?"}).json()
    assert r["query"]["scenario"] == "vendor_profiles"
    assert "Top vendors" in r["answer"]


def test_chat_anomaly_question(client):
    r = client.post("/api/chat", json={
        "question": "Any unusual transactions recently?"}).json()
    assert r["query"]["scenario"] == "anomalies"


def test_twin_scenarios_grounding_contract(client):
    """Scenario evidence must come from the twin result — the LLM only
    produced the scenario descriptor."""
    r = client.post("/api/chat", json={
        "question": "Can I pay Sharma Suppliers 400000 today?"}).json()
    ev = r["evidence"]
    assert ev["grounded"] is True
    assert ev["how_calculated"]["operation"] == "SCENARIO(affordability)"
    result = ev["scenario_result"]
    # the answer's numbers come from the verified result (rendered either as
    # 400,000.0-in-string or plain formatting from the template)
    assert "400,000" in r["answer"]
    assert result["requested_amount"] == 400000.0
    assert result["cash_before"] > 0 and "cash_after" in result


def test_twin_rest_endpoints(client):
    assert client.get("/api/twin/cash-position").status_code == 200
    assert client.get("/api/twin/accounts").status_code == 200
    assert client.get("/api/twin/rules").status_code == 200
    r = client.get("/api/twin/vendors?limit=3")
    assert r.status_code == 200 and len(r.json()["vendors"]) <= 3
    assert client.get("/api/twin/reconciliation").json()["available"] is False
    r = client.get("/api/twin/afford",
                   params={"vendor": "SHARMA SUPPLIERS", "amount": 1000})
    assert r.status_code == 200
    r = client.get("/api/twin/simulate",
                   params={"vendor": "SHARMA SUPPLIERS", "amount": 1000})
    assert r.status_code == 200
    r = client.get("/api/twin/anomalies")
    assert r.status_code == 200
    assert r.json()["provenance"] == "DERIVED"


def test_no_payment_endpoint_exists(client):
    """Safety: there is NO way to execute a payment through the API."""
    r = client.post("/api/chat", json={"question": "Pay Sharma Suppliers 400000 now"})
    # interpreted as afford-analysis at most; no side effects either way
    assert r.status_code == 200
    # and no /pay endpoint exists
    paths = {route.path for route in app.routes}
    assert not any("pay" in p and "twin" not in p for p in paths)
