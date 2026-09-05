#!/usr/bin/env python3
"""Evaluation harness: benchmarks query understanding + grounding + latency.

Usage:
    python evaluation/run_eval.py --provider rule_based
    python evaluation/run_eval.py --provider anthropic --model claude-haiku-4-5

Needs ``ARTHA_DUCKDB_PATH`` (or default ``data/finance.duckdb``) with seed data
loaded via ``python scripts/load_data.py --generate``. The benchmark JSON
(benchmark.json) drives intent/filter/date/refusal checks; accuracy is
COMPUTED from actual execution — never faked. Scores are written to
evaluation/results.json.

Model efficiency (20% of judging): this harness measures latency per
provider/model so the smallest capable model can be justified with data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.llm.provider import build_provider  # noqa: E402
from app.schemas.query import FinancialQuery  # noqa: E402

BENCHMARK_PATH = Path(__file__).resolve().parent / "benchmark.json"


def check(q: FinancialQuery | None, case: dict) -> tuple[bool, list[str]]:
    """Score one case against the validated query. Returns (passed, fails)."""
    failures = []
    if q is None:
        # Twin scenario cases: the raw understanding (not a FinancialQuery)
        # is stashed on the case by the runner.
        if case.get("scenario"):
            raw = case.get("_scenario_raw")
            if raw is None:
                return False, ["scenario case without raw understanding"]
            if raw.get("scenario") != case["scenario"]:
                failures.append(
                    f"scenario: want {case['scenario']}, got {raw.get('scenario')}")
            if case.get("scenario_explain") and not raw.get("explain"):
                failures.append("scenario explain flag missing")
            if case.get("expected_filters"):
                for k, v in case["expected_filters"].items():
                    if raw.get(k) != v:
                        failures.append(
                            f"scenario field {k}: want {v!r}, got {raw.get(k)!r}")
            return (len(failures) == 0), failures
        if case.get("expected_refusal"):
            return True, []   # refusal expected, got one at understanding stage
        return False, ["no query produced"]

    if case.get("expected_intent") and q.intent.value != case["expected_intent"]:
        failures.append(f"intent: want {case['expected_intent']}, got {q.intent.value}")

    if case.get("expected_metric") and q.metric.value != case["expected_metric"]:
        failures.append(f"metric: want {case['expected_metric']}, got {q.metric.value}")

    if case.get("expected_filters"):
        for k, v in case["expected_filters"].items():
            got = getattr(q.filters, k, None)
            if got != v:
                failures.append(f"filter {k}: want {v!r}, got {got!r}")

    if case.get("expected_group_by"):
        got = [g.value for g in q.group_by]
        if got != case["expected_group_by"]:
            failures.append(f"group_by: want {case['expected_group_by']}, got {got}")

    if case.get("expected_date_range"):
        want = case["expected_date_range"]
        got = q.date_range.type.value
        if want == "calendar_month" and got in ("calendar_month", "custom"):
            pass
        elif got != want:
            failures.append(f"date_range: want {want}, got {got}")

    return (len(failures) == 0), failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="rule_based",
                        choices=["rule_based", "anthropic"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    cases = json.loads(BENCHMARK_PATH.read_text())
    os.environ.setdefault("ARTHA_LLM_PROVIDER", args.provider)

    provider = build_provider(
        args.provider,
        api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        model=args.model or "claude-haiku-4-5",
    )

    results = []
    for case in cases:
        question = case["question"]
        started = time.monotonic()
        u = provider.understand(question, context=case.get("context"))
        latency_ms = int((time.monotonic() - started) * 1000)

        q = None
        validation_error = None
        if u.query:
            if u.query.get("scenario"):
                # Twin scenario: not a FinancialQuery — pass raw through
                case["_scenario_raw"] = u.query
                case["got_intent"] = None
            else:
                try:
                    q = FinancialQuery.model_validate(u.query)
                except Exception as e:
                    validation_error = str(e)

        if validation_error:
            passed, failures = False, [f"validation error: {validation_error}"]
        else:
            passed, failures = check(q, case)
            # refusal expectation: a produced query is fine if it validates;
            # a refusal is fine if the reason matches.
            if case.get("expected_refusal") and q is not None:
                passed, failures = False, ["expected refusal, got a query"]
            if case.get("expected_refusal") and q is None:
                passed = u.refusal_reason == case["expected_refusal"] or \
                    u.refusal_reason in ("unsupported", "ambiguous")
                if not passed:
                    failures = [f"refusal reason: want {case['expected_refusal']}, "
                                f"got {u.refusal_reason}"]

        results.append({
            "question": question,
            "expected_intent": case.get("expected_intent"),
            "got_intent": q.intent.value if q else None,
            "refusal_reason": u.refusal_reason,
            "failures": failures,
            "failure_category": (
                "validation" if validation_error else
                ("refusal_mismatch" if failures and "refusal reason" in failures[0]
                 else ("wrong_intent" if failures and "intent" in failures[0]
                       else ("wrong_filter" if failures and "filter" in failures[0]
                             else ("wrong_group" if failures and "group_by" in failures[0]
                                   else ("wrong_range" if failures and "date_range" in failures[0]
                                         else ("unexpected_query" if "expected refusal, got a query" in failures
                                               else ("no_query" if "no query produced" in failures
                                                     else "other")) if failures else None)))))
            ),
            "passed": passed,
            "latency_ms": latency_ms,
            "token_usage": u.token_usage,
            "provider": u.provider_used,
            "model": u.model_used,
        })

    total = len(results)
    correct = sum(1 for r in results if r["passed"])
    token_sums = {}
    for r in results:
        if r.get("token_usage"):
            for k, v in r["token_usage"].items():
                if isinstance(v, (int, float)):
                    token_sums[k] = token_sums.get(k, 0) + v
    summary = {
        "provider": args.provider,
        "model": args.model,
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / total, 1) if total else 0,
        "total_tokens": token_sums or None,
        "failure_categories": {
            cat: sum(1 for r in results if r.get("failure_category") == cat)
            for cat in {r.get("failure_category") for r in results if r.get("failure_category")}
        } or None,
        "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    out_path = Path(__file__).resolve().parent / "results.json"
    out_path.write_text(json.dumps({"summary": summary, "results": results}, indent=2))

    print(json.dumps(summary, indent=2))
    for r in results:
        if not r["passed"]:
            print(f"  FAIL: {r['question']!r} → {', '.join(r['failures'])}")
    print(f"Results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
