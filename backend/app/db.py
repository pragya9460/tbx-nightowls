"""DuckDB connection helpers — the only database Artha uses at runtime."""
from __future__ import annotations

from pathlib import Path

import duckdb
from sqlalchemy.orm import DeclarativeBase

from . import config
from .query_engine.duckdb_store import (
    build_duckdb_from_csvs,
    default_data_dir,
    default_duckdb_path,
    ensure_duckdb,
)


class Base(DeclarativeBase):
    """ORM metadata for the TBX models (SQLAlchemy builder; unused at runtime)."""


def duckdb_path() -> Path:
    if config.DUCKDB_PATH:
        return Path(config.DUCKDB_PATH).resolve()
    return default_duckdb_path()


def get_read_connection() -> duckdb.DuckDBPyConnection:
    """Open the finance DuckDB file read-only (chat / query path)."""
    path = ensure_duckdb(data_dir=default_data_dir(), db_path=duckdb_path())
    return duckdb.connect(str(path), read_only=True)


def bootstrap_duckdb(data_dir: Path | str | None = None) -> Path:
    """Create/refresh finance.duckdb from CSVs (startup / seed scripts)."""
    return build_duckdb_from_csvs(
        data_dir=data_dir or default_data_dir(),
        db_path=duckdb_path(),
    )
