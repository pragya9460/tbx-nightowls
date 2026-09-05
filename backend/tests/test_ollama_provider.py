"""Ollama provider unit tests (mocked HTTP — no live Ollama required)."""
from __future__ import annotations

import json
from io import BytesIO
from urllib.error import URLError

import pytest

from app.llm.provider import OllamaProvider, _extract_json_object


def test_extract_json_object_plain():
    data = _extract_json_object('{"intent":"transaction_summary","refusal":null}')
    assert data["intent"] == "transaction_summary"


def test_extract_json_object_fenced():
    raw = 'Here you go:\n```json\n{"intent": "account_balance"}\n```\n'
    assert _extract_json_object(raw)["intent"] == "account_balance"


def test_ollama_understand_success(monkeypatch):
    payload = {
        "intent": "transaction_summary",
        "metric": "transaction_amount",
        "aggregation": "sum",
        "filters": {"transaction_type": "debit"},
        "date_range": {"type": "calendar_month", "month": "july"},
        "group_by": [],
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            body = {"message": {"content": json.dumps(payload)}}
            return json.dumps(body).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _Resp()
    )
    u = OllamaProvider(model="qwen2.5-coder:7b").understand(
        "What are the expenses for july for vendors"
    )
    assert u.refusal_reason is None
    assert u.provider_used == "ollama"
    assert u.model_used == "qwen2.5-coder:7b"
    assert u.query["intent"] == "transaction_summary"
    assert u.query["filters"]["transaction_type"] == "debit"


def test_ollama_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    u = OllamaProvider(model="qwen2.5-coder:7b").understand("hello")
    assert u.refusal_reason == "unsupported"
    assert "Ollama" in (u.refusal_message or "")
