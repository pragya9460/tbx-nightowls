"""Evidence builder: turns a QueryResult into the evidence block shown in the
UI ("How I got this" + breakdown + source records).

All values in evidence come from the engine (already masked). The evidence
never contains more than MAX_RECORDS_SHOWN rows — large result sets are
summarized (see record_count) and paginated in the UI, never dumped.
"""
from __future__ import annotations

from ..schemas.query import FinancialQuery
from .result import QueryResult

MAX_RECORDS_SHOWN = 15


def build_evidence(q: FinancialQuery, result: QueryResult) -> dict:
    dr = q.date_range
    date_label = dr.label or (
        f"{dr.start.isoformat()} to {dr.end.isoformat()}"
        if dr.start and dr.end else "all time"
    )
    op = q.aggregation.value.upper()
    metric = q.metric.value

    filters = {k: v for k, v in q.filters.model_dump().items() if v is not None}
    # The evidence's filter line shows what was ACTUALLY queried — describe
    # masking in words, never echo raw sensitive values.
    if "utr_number" in filters:
        filters["utr_number"] = "(sensitive — matched in database, masked here)"

    evidence: dict = {
        "how_calculated": {
            "date_range": date_label,
            "operation": f"{op}({metric})" if op != "NONE" else f"LIST({metric})",
            "records_matched": result.summary.get(
                "record_count",
                len(result.records),
            ),
            "filters": filters,
            "sql": result.query_metadata.get("sql"),
            "cache_hit": bool(result.query_metadata.get("cache_hit")),
        },
        "source": "MySQL — TBX financial dataset (bank / account / transaction, deterministic query engine)",
        "summary": result.summary,
        "grounded": True,
    }
    if result.breakdown:
        evidence["breakdown"] = result.breakdown
    if result.records:
        evidence["records"] = result.records[:MAX_RECORDS_SHOWN]
        evidence["records_truncated"] = len(result.records) > MAX_RECORDS_SHOWN
    if result.query_metadata.get("is_comparison_of"):
        evidence["comparison_of"] = True
    return evidence
