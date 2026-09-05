"""Evidence builder: turns a QueryResult into the evidence block shown in the
UI ("How this was calculated" + breakdown + sample records)."""
from __future__ import annotations

from ..schemas.query import FinancialQuery
from .engine import QueryResult

MAX_RECORDS_SHOWN = 20


def build_evidence(q: FinancialQuery, result: QueryResult) -> dict:
    dr = q.date_range
    date_label = dr.label or (
        f"{dr.start.isoformat()} to {dr.end.isoformat()}"
        if dr.start and dr.end else "all time"
    )
    op = q.aggregation.value.upper()
    metric = q.metric.value

    evidence: dict = {
        "how_calculated": {
            "date_range": date_label,
            "operation": f"{op}({metric})" if op != "NONE" else f"LIST({metric})",
            "records_matched": result.summary.get(
                "record_count",
                len(result.records),
            ),
            "filters": {k: v for k, v in q.filters.model_dump().items() if v is not None},
        },
        "source": "PostgreSQL — artha financial dataset (deterministic query engine)",
        "grounded": True,
    }
    if result.breakdown:
        evidence["breakdown"] = result.breakdown
    if result.records:
        evidence["records"] = result.records[:MAX_RECORDS_SHOWN]
    if result.query_metadata.get("is_comparison_of"):
        evidence["comparison_of"] = result.query_metadata["is_comparison_of"]
    return evidence
