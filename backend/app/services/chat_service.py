"""Chat orchestration: question → understanding → validation → DuckDB → answer.

Every stage is deterministic except query-understanding, and even that output
must pass Pydantic validation before the Text-to-SQL compiler runs.
"""
from __future__ import annotations

import datetime as dt

from .. import config
from ..conversation.memory import ConversationStore
from ..llm.provider import QueryUnderstanding, build_provider
from ..query_engine.duckdb_engine import DuckDBQueryEngine
from ..query_engine.evidence import build_evidence
from ..schemas.query import (
    DateRangeType,
    FinancialQuery,
    QueryRefusalReason,
    previous_period,
    resolve_date_range,
    refusal,
    today as app_today,
)
from .answers import generate_answer


class ChatService:
    def __init__(self, engine: DuckDBQueryEngine | None = None):
        self.engine = engine or DuckDBQueryEngine.from_path(
            config.DUCKDB_PATH or None
        )

    def _provider(self):
        return build_provider(
            config.effective_provider(),
            api_key=config.ANTHROPIC_API_KEY,
            model=config.LLM_MODEL,
            max_retries=config.LLM_MAX_RETRIES,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )

    def handle(self, question: str, conversation_id: str | None,
               store: ConversationStore) -> dict:
        ctx = store.get(conversation_id)

        provider = self._provider()
        understanding: QueryUnderstanding = provider.understand(
            question, context=ctx.to_prompt_context()
        )

        if understanding.refusal_reason:
            return self._refusal_response(
                understanding, provider, conversation_id, store,
            )

        raw = understanding.query or {}
        try:
            dr = raw.get("date_range")
            if isinstance(dr, dict) and dr.get("type") in [
                t.value for t in DateRangeType
            ]:
                # Relative ranges resolve deterministically server-side —
                # the LLM never supplies final dates.
                raw["date_range"] = resolve_date_range(
                    app_today(), dr
                ).model_dump(mode="json", exclude_none=True)
            fq = FinancialQuery.model_validate(raw)
        except Exception as e:
            return self._refusal_response(
                understanding, provider, conversation_id, store,
                reason=QueryRefusalReason.INVALID_STRUCTURE,
                message=(
                    "I parsed your question but couldn't build a valid financial "
                    f"query from it ({type(e).__name__}). Please rephrase."
                ),
                extra={"validation_error": str(e)},
            )

        comparison_result = None
        if fq.intent.value == "comparison":
            base = fq.date_range
            if fq.comparison and fq.comparison.against == "previous_year" and base.start:
                prev_start = dt.date(base.start.year - 1, base.start.month, base.start.day)
                prev_end = dt.date(base.end.year - 1, base.end.month, base.end.day)
            else:
                pp = previous_period(base)
                prev_start, prev_end = pp.start, pp.end
            base_result = self.engine.execute(fq)
            comparison_result = self.engine.execute_comparison(
                fq, base_result, prev_start, prev_end
            )
            result = base_result
        else:
            result = self.engine.execute(fq)

        answer = generate_answer(fq, result, comparison_result)
        evidence = build_evidence(fq, result)
        if comparison_result is not None:
            comparison_evidence = build_evidence(
                fq, comparison_result
            )
            # the comparison block must describe the COMPARISON period, not
            # repeat the base period's dates
            cmp_meta = comparison_result.query_metadata.get("date_range", {})
            comparison_evidence["how_calculated"]["date_range"] = cmp_meta.get(
                "label"
            ) or comparison_evidence["how_calculated"]["date_range"]
            evidence["comparison"] = comparison_evidence

        if conversation_id:
            ctx.apply(fq, {"value": result.summary.get("value"),
                           "answer": answer[:120]})

        return {
            "conversation_id": conversation_id or "anonymous",
            "answer": answer,
            "evidence": evidence,
            "query": fq.model_dump(mode="json", exclude_none=True),
            "refusal": None,
            "meta": {
                "provider": understanding.provider_used,
                "model": understanding.model_used,
                "understanding_latency_ms": understanding.latency_ms,
                "grounded": True,
                "backend": "duckdb",
            },
        }

    def _refusal_response(self, understanding, provider, conversation_id, store,
                          reason: QueryRefusalReason | None = None, message: str | None = None,
                          extra: dict | None = None) -> dict:
        r = refusal(
            reason=reason or QueryRefusalReason(understanding.refusal_reason)
            if (reason or understanding.refusal_reason) in [m.value for m in QueryRefusalReason]
            else QueryRefusalReason.UNSUPPORTED_METRIC,
            message=message or understanding.refusal_message or "I can't answer that.",
            suggestions=understanding.suggestions or [],
            include_supported=(reason is None),
        )
        resp = {
            "conversation_id": conversation_id or "anonymous",
            "answer": r.message,
            "evidence": None,
            "query": None,
            "refusal": r.model_dump(),
            "meta": {
                "provider": understanding.provider_used,
                "model": understanding.model_used,
                "understanding_latency_ms": understanding.latency_ms,
                "grounded": False,
                "backend": "duckdb",
            },
        }
        if extra:
            resp["meta"].update(extra)
        return resp
