"""Chat orchestration: question → understanding → validation → MySQL → answer.

Every stage is deterministic except query-understanding, and even that output
must pass Pydantic validation before the Text-to-SQL compiler runs.
"""
from __future__ import annotations

import re

from .. import config
from ..conversation.memory import ConversationStore
from ..db import build_engine
from ..db_settings import resolve_database_url
from ..llm.provider import QueryUnderstanding, build_provider
from ..llm.normalize import normalize_llm_query
from ..query_engine.evidence import build_evidence
from ..query_engine.mysql_engine import MySQLQueryEngine
from ..schemas.query import (
    DateRangeType,
    FinancialQuery,
    QueryRefusalReason,
    parse_month_vs_month,
    resolve_comparison_range,
    resolve_date_range,
    refusal,
    today as app_today,
)
from .answers import generate_answer


class ChatService:
    def __init__(
        self,
        engine: MySQLQueryEngine | None = None,
        *,
        database_url: str | None = None,
    ):
        self._owns_engine = engine is None
        if engine is not None:
            self.engine = engine
        else:
            url = database_url or resolve_database_url()
            self.engine = build_engine(url)

    def close(self) -> None:
        if self._owns_engine:
            self.engine.close()

    def _provider(self):
        return build_provider(
            config.effective_provider(),
            api_key=config.ANTHROPIC_API_KEY,
            model=config.effective_model(),
            max_retries=config.LLM_MAX_RETRIES,
            timeout=config.LLM_TIMEOUT_SECONDS,
            ollama_base_url=config.OLLAMA_BASE_URL,
        )

    def handle(self, question: str, conversation_id: str | None,
               store: ConversationStore) -> dict:
        # Rebind engine if this conversation has a judge URL override.
        if conversation_id and self._owns_engine:
            try:
                url = resolve_database_url(conversation_id)
                if url != getattr(self.engine, "database_url", None):
                    self.engine.close()
                    self.engine = build_engine(url)
            except Exception:
                pass

        ctx = store.get(conversation_id)

        provider = self._provider()
        understanding: QueryUnderstanding = provider.understand(
            question, context=ctx.to_prompt_context()
        )

        if understanding.refusal_reason:
            return self._refusal_response(
                understanding, provider, conversation_id, store,
            )

        raw = normalize_llm_query(understanding.query or {})

        # Deterministic override: "July vs August" must not rely on flaky
        # previous_period anchoring (which often becomes July vs June).
        pair = parse_month_vs_month(question)
        if pair:
            m1, m2 = pair
            raw["intent"] = "comparison"
            raw.setdefault("metric", "transaction_amount")
            raw.setdefault("aggregation", "sum")
            filters = dict(raw.get("filters") or {})
            if re.search(
                r"\b(expense|expenses|spent|spend|spending|paid|debit)\b",
                question,
                re.IGNORECASE,
            ):
                filters.setdefault("transaction_type", "debit")
            raw["filters"] = filters
            raw["date_range"] = {"type": "calendar_month", "month": m1}
            raw["comparison"] = {"against": "named_month", "month": m2}
            raw.setdefault("group_by", [])

        try:
            dr = raw.get("date_range")
            if isinstance(dr, dict) and dr.get("type") in [
                t.value for t in DateRangeType
            ]:
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
                extra={
                    "validation_error": str(e),
                    "raw_query": understanding.query,
                    "normalized_query": raw,
                },
            )

        comparison_result = None
        if fq.intent.value == "comparison":
            cmp_dr = resolve_comparison_range(
                app_today(), fq.date_range, fq.comparison
            )
            base_result = self.engine.execute(fq)
            comparison_result = self.engine.execute_comparison(
                fq, base_result, cmp_dr.start, cmp_dr.end
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
                "backend": "mysql",
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
                "backend": "mysql",
            },
        }
        if extra:
            resp["meta"].update(extra)
        return resp
