"""Deterministic anomaly detection (bonus requirement).

Rule (documented, configurable, no ML, no LLM judgement):

    a transaction is ANOMALOUS when
        current_amount > anomaly_multiplier × historical_average
    where the historical average is computed over the same counterparty's
    transactions EXCLUDING the transaction under test, and history is only
    trusted when it has at least `min_history` records.

Insufficient history → no anomaly (never flagged, never guessed).
The LLM may later EXPLAIN a flagged anomaly; it never decides one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..query_engine.result import QueryResult

# Configurable threshold (env-overridable; documented in docs/financial-twin.md)
DEFAULT_MULTIPLIER = float(os.environ.get("ARTHA_ANOMALY_MULTIPLIER", "3.0"))
DEFAULT_MIN_HISTORY = int(os.environ.get("ARTHA_ANOMALY_MIN_HISTORY", "5"))


@dataclass
class AnomalyVerdict:
    is_anomalous: bool
    counterparty: str | None
    current_amount: float
    historical_average: float | None = None
    historical_max: float | None = None
    historical_count: int = 0
    ratio: float | None = None          # current / historical_average
    multiplier: float = DEFAULT_MULTIPLIER
    reason: str = ""
    history_sample: list[dict] = field(default_factory=list)  # evidence rows

    def to_dict(self) -> dict:
        return {
            "is_anomalous": self.is_anomalous,
            "counterparty": self.counterparty,
            "current_amount": self.current_amount,
            "historical_average": self.historical_average,
            "historical_max": self.historical_max,
            "historical_count": self.historical_count,
            "ratio": round(self.ratio, 2) if self.ratio is not None else None,
            "multiplier": self.multiplier,
            "reason": self.reason,
        }


def evaluate_transaction(
    current: dict,
    history: list[dict],
    *,
    multiplier: float | None = None,
    min_history: int | None = None,
) -> AnomalyVerdict:
    """Deterministic anomaly verdict for one transaction against its
    counterparty history.

    ``current``/``history`` rows need: description, transaction_amount,
    transaction_date. History MUST NOT contain the current transaction.
    """
    mult = multiplier if multiplier is not None else DEFAULT_MULTIPLIER
    min_hist = min_history if min_history is not None else DEFAULT_MIN_HISTORY

    from .vendor_intel import extract_counterparty

    cp = extract_counterparty(current.get("description"))
    amount = float(current.get("transaction_amount") or 0)

    if cp is None:
        return AnomalyVerdict(
            is_anomalous=False, counterparty=None, current_amount=amount,
            multiplier=mult,
            reason="counterparty could not be determined from the description",
        )

    hist_amounts = [float(h["transaction_amount"] or 0) for h in history]
    if len(hist_amounts) < min_hist:
        return AnomalyVerdict(
            is_anomalous=False, counterparty=cp, current_amount=amount,
            historical_count=len(hist_amounts), multiplier=mult,
            reason=(
                f"insufficient history ({len(hist_amounts)} records, "
                f"need {min_hist}) — anomaly check not applied"
            ),
        )

    avg = sum(hist_amounts) / len(hist_amounts)
    hmax = max(hist_amounts)
    if avg <= 0:
        return AnomalyVerdict(
            is_anomalous=False, counterparty=cp, current_amount=amount,
            historical_average=avg, historical_max=hmax,
            historical_count=len(hist_amounts), multiplier=mult,
            reason="historical average is zero — ratio undefined",
        )

    ratio = amount / avg
    is_anom = amount > mult * avg
    return AnomalyVerdict(
        is_anomalous=is_anom,
        counterparty=cp,
        current_amount=amount,
        historical_average=avg,
        historical_max=hmax,
        historical_count=len(hist_amounts),
        ratio=ratio,
        multiplier=mult,
        reason=(
            f"{amount:,.0f} is {ratio:.1f}× the historical average "
            f"({avg:,.0f}) — threshold {mult}×"
            if is_anom
            else f"{ratio:.1f}× the historical average — within the {mult}× threshold"
        ),
        # deterministic, capped evidence sample
        history_sample=sorted(
            history, key=lambda h: float(h["transaction_amount"] or 0), reverse=True
        )[:10],
    )
