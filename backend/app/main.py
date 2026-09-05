"""Artha — AI Finance Assistant. FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .api.routes import router

app = FastAPI(
    title=config.API_TITLE,
    version=config.API_VERSION,
    description=(
        "Finance assistant with a deterministic MySQL Text-to-SQL engine. "
        "The LLM only maps questions to structured FinancialQuery JSON; every "
        "number comes from compiled MySQL SELECT statements."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    """Verify MySQL connectivity; do not auto-seed (use scripts/load_data.py)."""
    try:
        from .db import build_engine

        eng = build_engine()
        try:
            eng.ping()
        finally:
            eng.close()
    except Exception as exc:  # pragma: no cover - surfaced via /api/health
        import logging

        logging.getLogger("artha").warning("MySQL not reachable at startup: %s", exc)
