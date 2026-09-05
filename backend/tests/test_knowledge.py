"""Knowledge layer (semantic RAG path): ingestion masking, retrieval,
unified /api/ask routing. ChromaDB runs ephemeral/in-memory via a temp dir;
no network needed (ONNX MiniLM downloads on first use — skipped if absent)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.knowledge import config as kcfg
from app.knowledge.ingestion import ingest_records, mask_account_number, mask_utr


@pytest.fixture()
def client_no_knowledge(monkeypatch):
    """Layer explicitly off — /api/ask semantic returns 'unavailable'."""
    monkeypatch.setenv("ARTHA_KNOWLEDGE_ENABLED", "0")
    monkeypatch.setattr(kcfg, "KNOWLEDGE_ENABLED", False)
    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture()
def knowledge_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_KNOWLEDGE_ENABLED", "1")
    monkeypatch.setattr(kcfg, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(kcfg, "CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(kcfg, "CHROMA_COLLECTION_NAME", "test_knowledge")
    # force a fresh collection per test
    from app.knowledge.store import _store

    _store._collection = None
    _store._client = None
    yield
    _store._collection = None
    _store._client = None


def test_mask_account_number_one_way():
    assert mask_account_number("3566392613993") == "XXXXX3993"
    assert mask_account_number(None) is None


def test_mask_utr_one_way():
    assert mask_utr("N123456789").startswith("N123")
    assert mask_utr("N123456789").endswith("89")
    assert "45678" not in mask_utr("N123456789")
    assert mask_utr("AB123") == "AB***"


def test_ingestion_masks_sensitive_columns(knowledge_env):
    records = [{
        "content": "account_number: 3566392613993\nutr_number: N123456789",
        "metadata": {
            "kind": "csv_row", "source": "t.csv",
            "account_number": "3566392613993", "utr_number": "N123456789",
        },
    }]
    ingest_records(records)
    from app.knowledge.store import get_store

    hits = get_store().query("account number", top_k=5, threshold=0.0)
    assert hits, "expected the row to be retrievable"
    blob = " ".join(h["content"] + str(h["metadata"]) for h in hits)
    assert "3566392613993" not in blob, "raw account number leaked into vector store"
    assert "N123456789" not in blob, "raw UTR leaked into vector store"
    assert "XXXXX3993" in blob


def test_ask_semantic_when_layer_disabled(client_no_knowledge):
    resp = client_no_knowledge.post("/api/ask", json={
        "question": "What is a UTR number?", "query_type": "semantic",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_type"] == "semantic"
    assert body["status"] == "unavailable"


def test_ask_semantic_with_ingested_knowledge(knowledge_env):
    ingest_records([{
        "content": "A UTR (Unique Transaction Reference) is a unique code "
                   "generated for every bank transaction in India.",
        "metadata": {"kind": "document", "source": "glossary.md"},
    }])
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/ask", json={
            "question": "What is a UTR number?",
            "query_type": "semantic",
            "top_k": 1,
            "threshold": 0.3,
            "filter": None,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_type"] == "semantic"
    assert body["status"] == "supported"
    assert body["sources"], "expected sources on a supported semantic answer"


def test_ask_auto_routes_grounded_question_to_analytics(knowledge_env):
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/ask", json={
            "question": "How much did I spend last month?", "query_type": "auto",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_type"] == "analytics"
    assert body["status"] in ("supported", "empty_data")
    assert body["evidence"] is not None


def test_ask_auto_falls_back_to_semantic_on_unsupported(knowledge_env):
    ingest_records([{
        "content": "Escrow accounts hold funds on behalf of parties until "
                   "contractual obligations are met.",
        "metadata": {"kind": "document", "source": "escrow-doc.md"},
    }])
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/ask", json={
            "question": "What is an escrow account?", "query_type": "auto",
        })
    assert resp.status_code == 200
    body = resp.json()
    # engine refuses escrow (unsupported domain) → semantic fallback
    assert body["query_type"] == "semantic"
    assert body["status"] in ("supported", "empty_data")


def test_ask_auto_keeps_real_zero_from_engine(knowledge_env):
    """A valid query that matches nothing must stay empty_data — never
    silently re-answered from the knowledge base."""
    from app.main import app

    ingest_records([{
        "content": "The word ZEBRA appears here.",
        "metadata": {"kind": "document", "source": "z.md"},
    }])
    with TestClient(app) as client:
        resp = client.post("/api/ask", json={
            "question": "Show transactions containing ZEBRA last month",
            "query_type": "auto",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_type"] == "analytics"
    assert body["status"] == "empty_data"


def test_knowledge_search_endpoint(knowledge_env):
    ingest_records([{
        "content": "Available balance is the money you can spend right now.",
        "metadata": {"kind": "document", "source": "glossary.md"},
    }])
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/knowledge/search", params={"q": "available balance"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
