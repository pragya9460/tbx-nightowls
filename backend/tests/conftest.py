"""Shared test fixtures: temp DuckDB seeded with deterministic TBX data."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["ARTHA_LLM_PROVIDER"] = "rule_based"
# Isolate tests from any developer Redis / leftover process cache.
os.environ.pop("ARTHA_REDIS_URL", None)
os.environ["ARTHA_QUERY_CACHE"] = "1"

from app.query_engine.duckdb_engine import DuckDBQueryEngine  # noqa: E402
from app.query_engine.duckdb_store import build_duckdb_from_rows  # noqa: E402
from app.services.seed_data import generate  # noqa: E402

# Fixed test window so date resolution tests are reproducible.
TEST_START = dt.date(2025, 9, 1)
TEST_END = dt.date(2026, 8, 31)


@pytest.fixture(autouse=True)
def _reset_query_cache():
    from app.query_engine import cache as cache_mod

    cache_mod.reset_cache_backend()
    yield
    cache_mod.reset_cache_backend()


@pytest.fixture(scope="session")
def seed_bundle():
    return generate(
        seed=42,
        n_accounts=12,
        n_transactions=600,
        start_date=TEST_START,
        end_date=TEST_END,
    )


@pytest.fixture(scope="session")
def duckdb_file(tmp_path_factory, seed_bundle):
    """Session-scoped DuckDB file on disk (for API TestClient / read_only opens)."""
    path = tmp_path_factory.mktemp("artha") / "test.finance.duckdb"
    con = build_duckdb_from_rows(
        seed_bundle.banks,
        seed_bundle.accounts,
        seed_bundle.transactions,
        db_path=path,
    )
    con.close()
    os.environ["ARTHA_DUCKDB_PATH"] = str(path)
    import app.config as cfg

    cfg.DUCKDB_PATH = str(path)
    return path


@pytest.fixture()
def duck_engine(seed_bundle):
    """Fresh in-memory DuckDB engine per test."""
    con = build_duckdb_from_rows(
        seed_bundle.banks,
        seed_bundle.accounts,
        seed_bundle.transactions,
    )
    eng = DuckDBQueryEngine.from_connection(con)
    yield eng
    eng.close()
    con.close()


@pytest.fixture()
def db(duck_engine):
    """Back-compat alias: tests that used a SQLAlchemy session now get DuckDB."""
    return duck_engine
