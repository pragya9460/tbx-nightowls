"""Interpretable confidence signalling (bonus requirement).

Deterministic categories — never fake probabilities:

    high        supported intent, valid query, executed, evidence present
    limited     valid query but thin data (matched records below threshold)
    no_matches  valid query, zero records matched (a real zero, not an error)
    none        nothing executed (ambiguous / unsupported / invalid refusal)

The category is computed from the SAME verified QueryResult the answer was
rendered from — the LLM has no input into it.
"""
from __future__ import annotations

from ..query_engine.result import QueryResult

# Below this many matched records, a valid result is flagged "limited":
# the computation is exact but the sample is thin enough that the user
# should read it with care.
LIMITED_DATA_THRESHOLD = 5

_BASIS = {
    "high": "supported query, executed deterministically, evidence attached",
    "limited": (
        "computation is exact but few records matched — treat as indicative"
    ),
    "no_matches": "valid query, no matching records in the dataset",
    "none": "nothing was executed — the question could not be mapped to a supported query",
}


def confidence_for_result(result: QueryResult | None) -> tuple[str, str]:
    """(confidence, basis) from a verified result. ``None`` result → none."""
    if result is None:
        return "none", _BASIS["none"]
    matched = result.summary.get("record_count")
    if matched is None:
        matched = len(result.records) + len(result.breakdown)
    if matched == 0 and not result.records and not result.breakdown:
        return "no_matches", _BASIS["no_matches"]
    if matched < LIMITED_DATA_THRESHOLD:
        return "limited", _BASIS["limited"]
    return "high", _BASIS["high"]


def confidence_for_refusal() -> tuple[str, str]:
    return "none", _BASIS["none"]
