"""Shared test fixtures: in-memory SQLite DB seeded with deterministic data."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before any app import: app.db reads DATABASE_URL at import time.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ARTHA_LLM_PROVIDER"] = "rule_based"

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Reconciliation,
    Transaction,
    Vendor,
    VendorPayout,
)
from app.services.seed_data import generate  # noqa: E402


@pytest.fixture(scope="session")
def seeded_db():
    """One engine + schema + deterministic seed for the whole test session."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    bundle = generate(seed=42, n_vendors=12, n_transactions=600,
                      start_date=dt.date(2025, 9, 1),
                      end_date=dt.date(2026, 8, 31))

    def _dateify(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            row = dict(row)
            for k, v in list(row.items()):
                if v == "":
                    row[k] = None
                elif isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
                    try:
                        row[k] = dt.date.fromisoformat(v)
                    except ValueError:
                        pass
            out.append(row)
        return out

    db = SessionLocal()
    db.bulk_insert_mappings(Vendor, bundle.vendors)
    db.bulk_insert_mappings(Transaction, _dateify(bundle.transactions))
    db.bulk_insert_mappings(VendorPayout, _dateify(bundle.payouts))
    db.bulk_insert_mappings(Reconciliation, _dateify(bundle.reconciliation))
    db.commit()
    yield db
    db.close()


@pytest.fixture()
def db(seeded_db):
    """Fresh session per test (same data)."""
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def vendor_names(db):
    return [v.vendor_name for v in db.query(Vendor.vendor_name).all()]
