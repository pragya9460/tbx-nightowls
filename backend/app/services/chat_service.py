"""Chat orchestration: question → understanding → validation → engine → answer.

This is the pipeline from spec §4. Every stage is deterministic except the
query-understanding step, and even that output must pass Pydantic validation
before execution.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..conversation.memory import ConversationStore
from ..llm.provider import QueryUnderstanding, build_provider
from ..query_engine.engine import FinancialQueryEngine
from ..query_engine.evidence import build_evidence
from ..schemas.query import (
    ComparisonSpec,
    FinancialQuery,
    QueryRefusalReason,
    previous_period,
    resolve_date_range,
    refusal,
)
from ..config import LLM_MODEL, LLM_MAX_RETRIES, LLM_TIMEOUT_SECONDS
from .answers import generate_answer


class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.engine = FinancialQueryEngine(db)

    def _provider(self):
        from .. import config

        provider_name = config.effective_provider()
        vendor_names = [
            name for (name,) in self.db.execute(
                __import__("sqlalchemy").select(
                    __import__("app.models", fromlist=["Vendor"]).Vendor.vendor_name
                )
            )
        ]
        return build_provider(
            provider_name,
            api_key=config.ANTHROPIC_API_KEY,
            model=config.LLM_MODEL,
            vendor_names=vendor_names,
            max_retries=config.LLM_MAX_RETRIES,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )

    def handle(self, question: str, conversation_id: str | None,
               store: ConversationStore) -> dict:
        ctx = store.get(conversation_id)

        # 1. Understand (LLM or rule-based)
        provider = self._provider()
        understanding: QueryUnderstanding = provider.understand(
            question, context=ctx.to_prompt_context()
        )

        # 2. Refusals from understanding stage
        if understanding.refusal_reason:
            return self._refusal_response(
                understanding, provider, conversation_id, store,
            )

        # 3. Validate strictly (never execute unvalidated model output)
        raw = understanding.query or {}
        try:
            if isinstance(raw.get("date_range"), dict) and raw["date_range"].get("type") in (
                "calendar_month", "last_n_months", "custom", "all_time",
                "month_before_previous",
            ) and not raw["date_range"].get("start") and not raw["date_range"].get("end") \
               and raw["date_range"].get("type") not in ("all_time", "last_n_months"):
                # resolve relative ranges deterministically before validation
                raw["date_range"] = resolve_date_range(
                    dt.date.today(), raw["date_range"]
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

        # 4. Comparison: compute both periods
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

        # 5. Answer + evidence from computed results
        answer = generate_answer(fq, result, comparison_result)
        evidence = build_evidence(fq, result)
        if comparison_result is not None:
            evidence["comparison"] = build_evidence(fq, comparison_result)

        # 6. Update structured conversation memory
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
            include_supported=(reason is None),  # show capabilities for unsupported
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
            },
        }
        if extra:
            resp["meta"].update(extra)
        return resp
