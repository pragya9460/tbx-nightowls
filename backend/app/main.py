"""Artha — AI Finance Assistant. FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from . import config
from .api.routes import router
from .api.ask_router import router as ask_router

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
app.include_router(ask_router)


@app.get("/scalar", include_in_schema=False)
def scalar_docs() -> HTMLResponse:
    """Scalar API reference (loads from CDN, renders this app's OpenAPI spec)."""
    return HTMLResponse(
        """<!doctype html>
<html>
  <head>
    <title>Artha API — Scalar</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script
      id="api-reference"
      data-url="/openapi.json"
      data-configuration='{"theme":"purple","layout":"modern","showConsoleButton":false}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""
    )


@app.on_event("startup")
def on_startup() -> None:
    """Verify MySQL connectivity; do not auto-seed (use scripts/load_data.py).

    The semantic knowledge layer (RAG) warms up only when
    ARTHA_KNOWLEDGE_ENABLED=1 — the grounded demo stack is unchanged
    otherwise.
    """
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

    from .knowledge import config as kcfg

    if kcfg.KNOWLEDGE_ENABLED and kcfg.KNOWLEDGE_DATA_DIR:
        import logging

        logger = logging.getLogger("artha.knowledge")
        try:
            from .knowledge.ingestion import ingest_directory

            # Re-seed on boot so documents added to the mounted dir are
            # picked up without a manual ingest call.
            count = ingest_directory()
            logger.info("knowledge layer seeded %d chunks from %s",
                        count, kcfg.KNOWLEDGE_DATA_DIR)
        except Exception as exc:
            logger.warning("knowledge layer startup seeding failed: %s", exc)
