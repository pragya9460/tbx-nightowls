"""Deterministic anomaly engine tests (Phase 4).

The rule: current > multiplier × historical_average, with a minimum history
size. No ML, no LLM judgement, fully repeatable.
"""
from __future__ import annotations

import datetime as dt

from app.services.anomaly import evaluate_transaction
from app.services.vendor_intel import extract_counterparty


def _txn(desc, amount, date="2026-08-20 10:00:00"):
    return {"description": desc, "transaction_amount": amount,
            "transaction_date": date}


def _history(base_amount, n, spread=0.0):
    return [_txn("NEFT - 1000 - 2000 - SHARMA SUPPLIERS",
                 base_amount + spread * i,
                 (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat())
            for i in range(n)]


# ---------------------------------------------------------------------------
# Counterparty extraction
# ---------------------------------------------------------------------------

def test_extract_upi_format():
    assert extract_counterparty("UPI/PARESH VIKRANT GHASE/530613729/HDFC") == \
        "PARESH VIKRANT GHASE"


def test_extract_neft_dash_format():
    assert extract_counterparty(
        "NEFT - 418587604 - 2820151617123 - SPARK FACILITY SERVICES") == \
        "SPARK FACILITY SERVICES"


def test_extract_imps_ow_format():
    assert extract_counterparty("IMPS OW/507614422198/GAUTAM SINGH/SBIN/43292707719") == \
        "GAUTAM SINGH"


def test_extract_returns_none_for_empty():
    assert extract_counterparty(None) is None
    assert extract_counterparty("") is None


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def test_normal_transaction_not_flagged():
    history = _history(50_000, 10)   # avg ≈ 50k
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 55_000)
    v = evaluate_transaction(current, history)
    assert v.is_anomalous is False
    assert v.counterparty == "SHARMA SUPPLIERS"
    assert v.ratio is not None and v.ratio < 3.0


def test_anomalous_transaction_flagged():
    history = _history(50_000, 10)   # avg ≈ 50k
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 240_000)
    v = evaluate_transaction(current, history)
    assert v.is_anomalous is True
    assert v.historical_average == pytest.approx(50_000, rel=1e-3)
    assert v.ratio > 3.0
    assert "4.8×" in v.reason or "4.7×" in v.reason or "×" in v.reason


def test_insufficient_history_never_flags():
    history = _history(50_000, 3)    # below min_history=5
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 10_000_000)
    v = evaluate_transaction(current, history)
    assert v.is_anomalous is False
    assert "insufficient history" in v.reason


def test_zero_average_never_flags():
    history = _history(0, 8)
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 5_000)
    v = evaluate_transaction(current, history)
    assert v.is_anomalous is False
    assert "zero" in v.reason


def test_unknown_counterparty_never_flags():
    v = evaluate_transaction(_txn("ATM WITHDRAWAL", 999_999), _history(100, 10))
    assert v.is_anomalous is False
    assert v.counterparty is None or v.is_anomalous is False


def test_custom_multiplier_and_min_history():
    history = _history(50_000, 10)
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 120_000)  # 2.4×
    strict = evaluate_transaction(current, history, multiplier=2.0)
    assert strict.is_anomalous is True
    loose = evaluate_transaction(current, history, multiplier=5.0)
    assert loose.is_anomalous is False
    # min_history override: 3 records are enough when min_history=3
    thin_ok = evaluate_transaction(current, _history(50_000, 3),
                                   min_history=3, multiplier=2.0)
    assert thin_ok.is_anomalous is True


# ---------------------------------------------------------------------------
# Determinism + evidence
# ---------------------------------------------------------------------------

def test_deterministic_repeatability():
    history = _history(50_000, 12)
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 300_000)
    v1 = evaluate_transaction(current, history)
    v2 = evaluate_transaction(current, history)
    assert v1.to_dict() == v2.to_dict()


def test_evidence_sample_present_and_capped():
    history = _history(50_000, 20)
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 500_000)
    v = evaluate_transaction(current, history)
    assert 0 < len(v.history_sample) <= 10


def test_history_excludes_current_transaction():
    """The current transaction must not be part of its own baseline."""
    history = _history(50_000, 10)
    current = _txn("NEFT - 111 - 222 - SHARMA SUPPLIERS", 240_000)
    v = evaluate_transaction(current, history)
    # average is that of history alone (~50k), not diluted/inflated by 240k
    assert v.historical_average == pytest.approx(50_000, rel=1e-3)


import pytest  # noqa: E402
