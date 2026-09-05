"""MySQL connection helpers — the only database Artha uses at runtime."""
from __future__ import annotations

from pathlib import Path

from . import config
from .query_engine.mysql_engine import MySQLQueryEngine
from .query_engine.mysql_store import (
    default_data_dir,
    default_database_url,
    load_csvs_into_mysql,
)
from .query_engine.mysql_url import mask_mysql_url


def build_engine(database_url: str | None = None) -> MySQLQueryEngine:
    url = (database_url or config.DATABASE_URL or default_database_url()).strip()
    return MySQLQueryEngine.from_url(url)


def bootstrap_mysql(
    data_dir: Path | str | None = None,
    *,
    url: str | None = None,
    drop: bool = True,
) -> dict[str, int]:
    """Create/refresh MySQL tables from CSVs (startup / seed scripts)."""
    return load_csvs_into_mysql(
        data_dir=data_dir or default_data_dir(),
        url=url or config.DATABASE_URL or default_database_url(),
        drop=drop,
    )


def masked_default_url() -> str:
    try:
        return mask_mysql_url(config.DATABASE_URL or default_database_url())
    except Exception:
        return "mysql://***"
