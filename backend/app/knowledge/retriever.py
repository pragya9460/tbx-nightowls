"""Semantic answer generation: retrieve → LLM with context-only grounding.

The LLM sees ONLY the retrieved passages. If nothing relevant is found, the
caller gets an honest empty state — no LLM call, no invented answer. This is
the knowledge-layer counterpart of the financial engine's grounding contract:
the model narrates supplied evidence; it never reaches the database.
"""
from __future__ import annotations

import logging

from .. import config
from . import config as kcfg
from .store import KnowledgeUnavailable, get_store

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Artha's knowledge assistant. Answer ONLY from the provided "
    "context passages. If the context does not contain the answer, say so "
    "plainly — do not guess, do not use outside knowledge. Be concise. "
    "When a passage supports a claim, mention its source name."
)


def _llm_answer(question: str, context: str) -> str | None:
    """Ask Claude to answer from the passage context. Returns None if the
    Anthropic key is absent or the call fails — the caller falls back to an
    extractive answer from the passages themselves."""
    if not config.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.effective_model()
            if config.effective_provider() == "anthropic"
            else "claude-haiku-4-5",
            max_tokens=600,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Context passages:\n\n{context}\n\nQuestion: {question}",
            }],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()
        return text or None
    except Exception as exc:
        logger.warning("knowledge LLM call failed, falling back to extractive: %s", exc)
        return None


def _extractive_answer(question: str, hits: list[dict]) -> str:
    """No-LLM fallback: quote the top passage(s) with their sources."""
    top = hits[0]
    src = top["metadata"].get("source", "unknown source")
    snippet = top["content"][:400]
    more = f" (+{len(hits) - 1} more passage(s) below)" if len(hits) > 1 else ""
    return (
        f"Closest match in the knowledge base (from {src}{more}): “{snippet}”"
    )


def ask(
    question: str,
    top_k: int | None = None,
    threshold: float | None = None,
    where: dict | None = None,
) -> dict:
    """Full semantic path. Returns {answer, status, sources, meta}.

    status:
      - "supported"     — passages found and an answer produced
      - "empty_data"    — knowledge base has no passages above threshold
      - "unavailable"   — semantic layer disabled / vector store not servable
    """
    top_k = top_k or kcfg.TOP_K_DEFAULT
    threshold = kcfg.SIMILARITY_THRESHOLD_DEFAULT if threshold is None else threshold

    try:
        hits = get_store().query(question, top_k=top_k, threshold=threshold, where=where)
    except KnowledgeUnavailable as exc:
        return {
            "answer": (
                f"I can't answer that from the knowledge base right now ({exc}). "
                "Financial questions still work through the grounded engine."
            ),
            "status": "unavailable",
            "sources": [],
            "meta": {"grounded": True, "backend": "knowledge", "path": "semantic"},
        }

    if not hits:
        return {
            "answer": (
                "No relevant information found in the knowledge base for that "
                "question. Try rephrasing, or ingest documents via "
                "POST /api/knowledge/ingest."
            ),
            "status": "empty_data",
            "sources": [],
            "meta": {
                "grounded": True,
                "backend": "knowledge",
                "path": "semantic",
                "top_k": top_k,
                "threshold": threshold,
            },
        }

    context = "\n\n---\n\n".join(
        f"[{h['metadata'].get('source', 'unknown')}]\n{h['content']}" for h in hits
    )
    answer = _llm_answer(question, context) or _extractive_answer(question, hits)

    sources = [
        {
            "content": h["content"][:300],
            "metadata": h["metadata"],
            "similarity": h["similarity"],
        }
        for h in hits
    ]
    return {
        "answer": answer,
        "status": "supported",
        "sources": sources,
        "meta": {
            "grounded": True,
            "backend": "knowledge",
            "path": "semantic",
            "top_k": top_k,
            "threshold": threshold,
            "passages": len(hits),
        },
    }


def search(question: str, top_k: int | None = None, threshold: float | None = None,
           where: dict | None = None) -> list[dict]:
    """Raw similarity search — no LLM."""
    top_k = top_k or kcfg.TOP_K_DEFAULT
    threshold = kcfg.SIMILARITY_THRESHOLD_DEFAULT if threshold is None else threshold
    return get_store().query(question, top_k=top_k, threshold=threshold, where=where)
