"""Ingest CSVs into PostgreSQL.

Works for BOTH the synthetic seed and the official hackathon dataset, as long
as files share the documented column names (see app/services/seed_data.py
CSV_COLUMNS). Missing vendor_id / description / currency are tolerated.

Usage:
    python scripts/load_data.py --data-dir ../data            # from backend/
    python scripts/load_data.py --drop                        # recreate tables
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Reconciliation,
    Transaction,
    Vendor,
    VendorPayout,
)


def parse_date(v: str) -> dt.date | None:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return dt.date.fromisoformat(v)


def parse_amount(v: str) -> float:
    v = (v or "").strip().replace(",", "").replace("₹", "")
    return float(Decimal(v)) if v else 0.0


def load_table(db, model, path: Path, date_fields: dict[str, str],
               float_fields: list[str], conflict_cols: list[str]):
    if not path.exists():
        print(f"  ! {path.name} not found, skipping")
        return 0
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f):
            row = {}
            for k, val in rec.items():
                if k is None:
                    continue
                k = k.strip()
                if k in date_fields:
                    row[k] = parse_date(val)
                elif k in float_fields:
                    row[k] = parse_amount(val)
                else:
                    row[k] = (val or "").strip() or None
            if all(row.get(c) is not None for c in conflict_cols):
                rows.append(row)
    if not rows:
        print(f"  ! {path.name} had no usable rows")
        return 0
    db.bulk_insert_mappings(model, rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("ARTHA_DATA_DIR", "../data"))
    parser.add_argument("--drop", action="store_true", help="drop and recreate all tables")
    parser.add_argument("--generate", action="store_true",
                        help="generate synthetic seed CSVs into data-dir first")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()

    if args.generate:
        from app.services.seed_data import generate, to_csv_rows
        bundle = generate(seed=args.seed)
        data_dir.mkdir(parents=True, exist_ok=True)
        for fname, rows in to_csv_rows(bundle).items():
            with open(data_dir / fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(next(
                    c for fn, c in __import__(
                        "app.services.seed_data", fromlist=["CSV_COLUMNS"]
                    ).CSV_COLUMNS.items() if fn == fname
                ))
                w.writerows(rows)
        print(f"Generated synthetic seed data in {data_dir}")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Tables created")

    db = SessionLocal()
    try:
        n_v = load_table(db, Vendor, data_dir / "vendors.csv", {}, [],
                         ["vendor_id", "vendor_name"])
        n_t = load_table(
            db, Transaction, data_dir / "transactions.csv",
            {"transaction_date": "d", "reconciled_date": "d"},
            ["amount"], ["transaction_id"],
        )
        n_p = load_table(
            db, VendorPayout, data_dir / "vendor_payouts.csv",
            {"payout_date": "d"}, ["amount"], ["payout_id"],
        )
        n_r = load_table(
            db, Reconciliation, data_dir / "reconciliation.csv",
            {"reconciled_date": "d"}, [], ["reconciliation_id"],
        )
        db.commit()
        print(f"Loaded: {n_v} vendors, {n_t} transactions, {n_p} payouts, {n_r} reconciliation rows")
    except Exception as e:
        db.rollback()
        print(f"Load failed: {e}")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
