"""Artha — AI Finance Assistant. FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .api.routes import router
from .db import Base, engine

app = FastAPI(
    title=config.API_TITLE,
    version=config.API_VERSION,
    description=(
        "Finance assistant with a deterministic query engine. The LLM only "
        "maps questions to structured queries; every number comes from PostgreSQL."
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
    # Tables are created by scripts/load_data.py; create defensively for
    # first-run convenience (empty DB still lets the API boot).
    Base.metadata.create_all(engine)
