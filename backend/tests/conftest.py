"""Shared test fixtures: MySQL seeded with deterministic TBX data.

Requires a running MySQL reachable via ARTHA_DATABASE_URL (default
mysql://artha:artha@127.0.0.1:3306/artha). Tests use a separate database
``artha_test`` so the demo DB is not wiped.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pymysql
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["ARTHA_LLM_PROVIDER"] = "rule_based"
os.environ.pop("ARTHA_REDIS_URL", None)
os.environ["ARTHA_QUERY_CACHE"] = "1"

from app.query_engine.mysql_engine import MySQLQueryEngine  # noqa: E402
from app.query_engine.mysql_store import (  # noqa: E402
    apply_schema,
    connect,
    insert_accounts,
    insert_banks,
    insert_transactions,
    truncate_all,
)
from app.query_engine.mysql_url import parse_mysql_url  # noqa: E402
from app.services.seed_data import generate  # noqa: E402

TEST_START = dt.date(2025, 9, 1)
TEST_END = dt.date(2026, 8, 31)
TEST_DB_NAME = "artha_test"


def _base_url() -> str:
    return os.environ.get(
        "ARTHA_DATABASE_URL",
        "mysql://artha:artha@127.0.0.1:3306/artha",
    ).strip()


def _test_url() -> str:
    raw = _base_url()
    parsed = urlparse(raw if "://" in raw else f"mysql://{raw}")
    return urlunparse(parsed._replace(path=f"/{TEST_DB_NAME}"))


def _ensure_test_database() -> str:
    url = _test_url()
    params = parse_mysql_url(_base_url())
    admin = pymysql.connect(
        host=params.host,
        port=params.port,
        user=params.user,
        password=params.password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with admin.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        admin.close()
    return url


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
def mysql_url(seed_bundle):
    """Session-scoped seeded MySQL test database."""
    try:
        url = _ensure_test_database()
    except Exception as exc:
        pytest.skip(f"MySQL not available: {exc}")

    con = connect(url, autocommit=False)
    try:
        apply_schema(con)
        truncate_all(con)
        insert_banks(con, seed_bundle.banks)
        insert_accounts(con, seed_bundle.accounts)
        insert_transactions(con, seed_bundle.transactions)
        con.commit()
    except Exception as exc:
        con.rollback()
        con.close()
        pytest.skip(f"MySQL seed failed: {exc}")
    finally:
        try:
            con.close()
        except Exception:
            pass

    os.environ["ARTHA_DATABASE_URL"] = url
    import app.config as cfg

    cfg.DATABASE_URL = url
    return url


# Back-compat alias used by older tests
@pytest.fixture(scope="session")
def duckdb_file(mysql_url):
    return mysql_url


@pytest.fixture()
def mysql_engine(mysql_url):
    eng = MySQLQueryEngine.from_url(mysql_url)
    yield eng
    eng.close()


@pytest.fixture()
def duck_engine(mysql_engine):
    """Back-compat alias: engine fixture name from DuckDB era."""
    return mysql_engine


@pytest.fixture()
def db(mysql_engine):
    return mysql_engine
