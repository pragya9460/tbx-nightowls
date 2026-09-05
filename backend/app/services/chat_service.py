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
from .confidence import confidence_for_result


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

        raw = understanding.query or {}
        if raw.get("scenario"):
            return self._handle_scenario(
                raw, understanding, conversation_id, store, ctx
            )
        raw = normalize_llm_query(raw)

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

        # Answer status (interpretable signals, never fake probabilities):
        #   supported  — exact supported query, deterministic result
        #   empty_data — valid query, zero matching records (a real zero)
        # Ambiguous/unsupported/invalid questions never reach this point.
        empty = (
            result.summary.get("record_count") == 0
            and not result.records
            and not result.breakdown
        )
        status = "empty_data" if empty else "supported"
        confidence, confidence_basis = confidence_for_result(result)

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
            "status": status,
            "confidence": confidence,
            "confidence_basis": confidence_basis,
            "meta": {
                "provider": understanding.provider_used,
                "model": understanding.model_used,
                "understanding_latency_ms": understanding.latency_ms,
                "token_usage": understanding.token_usage,
                "grounded": True,
                "backend": "mysql",
            },
        }

    # ----- Financial Twin scenarios (Phases 5–8) --------------------------------

    def _handle_scenario(self, raw: dict, understanding: QueryUnderstanding,
                         conversation_id: str | None, store: ConversationStore,
                         ctx) -> dict:
        """Execute a twin scenario through its deterministic engine and render
        a template answer from the verified result. Same grounding contract:
        the LLM produced only the scenario descriptor."""
        from ..services.financial_twin import FinancialTwinEngine
        from ..services.answers import format_inr

        twin = FinancialTwinEngine(self.engine)
        scenario = raw["scenario"]
        try:
            if scenario == "affordability":
                result = twin.can_i_afford(raw["vendor"], float(raw["amount"]))
                answer = self._afford_answer(result)
                status, confidence, basis = (
                    ("supported", "high",
                     "deterministic affordability analysis from the twin engine")
                )
            elif scenario == "what_if":
                result = twin.simulate_payment(raw["vendor"], float(raw["amount"]))
                answer = self._whatif_answer(result)
                status, confidence, basis = (
                    ("supported", "high",
                     "deterministic what-if simulation from the twin engine")
                )
            elif scenario == "cash_position":
                result = twin.cash_position()
                answer = self._cash_answer(result, raw.get("explain"))
                status, confidence, basis = (
                    ("supported", "high",
                     "deterministic cash-position computation from the twin engine")
                )
            elif scenario == "vendor_profiles":
                result = twin.vendor_profiles(limit=10)
                top = result["vendors"][:3]
                answer = (
                    "Top vendors by spend (derived from transaction data): "
                    + ", ".join(
                        f"{v['vendor']} ({format_inr(v['total_spend'])})"
                        for v in top
                    ) + ". Full breakdown below."
                    if top else "No vendor history found in the dataset."
                )
                status, confidence, basis = (
                    ("supported", "high",
                     "vendor profiles derived deterministically from transactions")
                )
            elif scenario == "anomalies":
                anomalies = self._scan_anomalies(twin)
                if anomalies:
                    a = anomalies[0]
                    answer = (
                        f"⚠ Unusual transaction: {format_inr(a['current_amount'])} "
                        f"to {a['counterparty']} — {a['reason']}."
                    )
                else:
                    answer = ("No unusual transactions found in the recent "
                              "history I checked (threshold-based rule).")
                result = {"anomalies": anomalies}
                status, confidence, basis = (
                    ("supported", "high",
                     "deterministic anomaly rule over transaction history")
                )
            else:
                return self._refusal_response(
                    understanding, None, conversation_id, store,
                    reason=QueryRefusalReason.UNSUPPORTED_METRIC,
                    message="That scenario type isn't supported yet.",
                )
        finally:
            pass  # engine lifecycle owned by the route handler

        if conversation_id:
            ctx.apply_scenario(scenario, answer[:120])

        return {
            "conversation_id": conversation_id or "anonymous",
            "answer": answer,
            "evidence": {
                "how_calculated": {
                    "date_range": "current position",
                    "operation": f"SCENARIO({scenario})",
                    "records_matched": (
                        len(result.get("vendors", []))
                        if scenario == "vendor_profiles"
                        else len(result.get("anomalies", []))
                        if scenario == "anomalies"
                        else 1
                    ),
                    "filters": {
                        k: v for k, v in raw.items()
                        if k in ("vendor", "amount", "explain")
                    },
                },
                "source": (
                    "Financial Twin — deterministic engine "
                    "(accounts/rules/reserves from labelled demo config; "
                    "amounts from the dataset)"
                ),
                "grounded": True,
                "scenario_result": result,
            },
            "query": raw,
            "refusal": None,
            "status": status,
            "confidence": confidence,
            "confidence_basis": basis,
            "meta": {
                "provider": understanding.provider_used,
                "model": understanding.model_used,
                "understanding_latency_ms": understanding.latency_ms,
                "token_usage": understanding.token_usage,
                "grounded": True,
                "backend": "financial_twin",
            },
        }

    @staticmethod
    def _afford_answer(r: dict) -> str:
        from ..services.answers import format_inr

        verdict = (
            "Yes — that payment is affordable."
            if r["affordable"] and not r["approval_required"]
            else "Yes, but it will require approval."
            if r["affordable"]
            else "Not safely based on your current financial position."
        )
        reasons = " ".join(r["reasons"])
        return f"{verdict} {reasons}".strip()

    @staticmethod
    def _whatif_answer(r: dict) -> str:
        from ..services.answers import format_inr

        ro = r["rules_outcome"]
        return (
            f"Before payment: {format_inr(r['before']['true_available_cash'])}. "
            f"Payment: {format_inr(r['payment_amount'])}. "
            f"After payment: {format_inr(r['after']['true_available_cash'])}. "
            f"Reserves {ro['payroll_reserve']}; minimum buffer "
            f"{ro['minimum_buffer']}; approval {ro['approval']}. "
            f"This is a static simulation — no payment was executed."
        )

    @staticmethod
    def _cash_answer(r: dict, explain: bool) -> str:
        from ..services.answers import format_inr

        base = (
            f"Your true available cash is {format_inr(r['true_available_cash'])}: "
            f"{format_inr(r['available_balance'])} across accounts, minus "
            f"{format_inr(r['protected_reserves'])} in protected reserves."
        )
        if explain:
            base += (
                " Available cash is lower than the raw total because protected "
                "reserves (payroll, GST) are earmarked and excluded."
            )
        return base

    @staticmethod
    def _scan_anomalies(twin) -> list[dict]:
        return twin.scan_anomalies(limit=5)

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
            "status": {
                "unsupported_metric": "unsupported",
                "unsupported_field": "unsupported",
                "ambiguous": "ambiguous",
                "invalid_structure": "invalid",
                "no_data": "empty_data",
            }.get(r.reason.value, "unsupported"),
            "confidence": "none",
            "confidence_basis": (
                "the question could not be mapped to a supported query; nothing was executed"
            ),
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
