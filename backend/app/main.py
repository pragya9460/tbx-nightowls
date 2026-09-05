"""Artha — AI Finance Assistant. FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .api.routes import router
from .db import bootstrap_duckdb, duckdb_path
from .query_engine.duckdb_store import default_data_dir

app = FastAPI(
    title=config.API_TITLE,
    version=config.API_VERSION,
    description=(
        "Finance assistant with a deterministic DuckDB Text-to-SQL engine. "
        "The LLM only maps questions to structured FinancialQuery JSON; every "
        "number comes from compiled DuckDB SQL."
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
    """Ensure finance.duckdb exists (build from CSVs if missing)."""
    path = duckdb_path()
    if not path.exists():
        data = config.DATA_DIR or default_data_dir()
        bootstrap_duckdb(data_dir=data)
