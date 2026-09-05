"""Shared test fixtures: in-memory SQLite DB seeded with deterministic TBX data."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before any app import: app.db reads the DB URL at import time.
os.environ["ARTHA_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ARTHA_LLM_PROVIDER"] = "rule_based"

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Bank, Transaction  # noqa: E402
from app.services.seed_data import generate  # noqa: E402

# Fixed test window so date resolution tests are reproducible.
TEST_START = dt.date(2025, 9, 1)
TEST_END = dt.date(2026, 8, 31)


@pytest.fixture(scope="session")
def seeded_db():
    """One engine + schema + deterministic seed for the whole test session."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    bundle = generate(seed=42, n_accounts=12, n_transactions=600,
                      start_date=TEST_START, end_date=TEST_END)

    def _datetimeify(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            row = dict(row)
            for k, v in list(row.items()):
                if v == "":
                    row[k] = None
                elif isinstance(v, str) and k == "transaction_date":
                    row[k] = dt.datetime.fromisoformat(v)
            out.append(row)
        return out

    db = SessionLocal()
    db.bulk_insert_mappings(Bank, bundle.banks)
    db.bulk_insert_mappings(Account, bundle.accounts)
    db.bulk_insert_mappings(Transaction, _datetimeify(bundle.transactions))
    db.commit()
    yield db
    db.close()


@pytest.fixture()
def db(seeded_db):
    """Fresh session per test (same data)."""
    session = SessionLocal()
    yield session
    session.close()
