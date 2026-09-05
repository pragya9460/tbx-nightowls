"""Ingest CSVs into the database (MySQL in production, SQLite for tests).

Works for BOTH the synthetic seed and the official TBX dataset, as long as
files share the documented column names (see app/services/seed_data.py
CSV_COLUMNS). Missing reference/UTR/description are tolerated; rows missing
required keys are skipped and reported.

Usage:
    python scripts/load_data.py --data-dir ../data            # from backend/
    python scripts/load_data.py --drop                        # recreate tables
    python scripts/load_data.py --generate --drop             # synthetic seed
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Bank, Transaction  # noqa: E402
from app.services.seed_data import CSV_COLUMNS  # noqa: E402


def parse_date(v: str) -> dt.datetime | None:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(v, fmt)
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def parse_amount(v: str) -> float:
    v = (v or "").strip().replace(",", "").replace("₹", "")
    if not v:
        return 0.0
    try:
        return float(Decimal(v))
    except InvalidOperation:
        return 0.0


def load_table(db, model, path: Path, date_fields: dict[str, str],
               float_fields: list[str], required_cols: list[str]):
    if not path.exists():
        print(f"  ! {path.name} not found, skipping")
        return 0
    rows = []
    skipped = 0
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
                elif k == "program_id":
                    row[k] = int(val) if (val or "").strip() else 0
                else:
                    row[k] = (val or "").strip() or None
            if all(row.get(c) is not None for c in required_cols):
                rows.append(row)
            else:
                skipped += 1
    if not rows:
        print(f"  ! {path.name} had no usable rows")
        return 0
    db.bulk_insert_mappings(model, rows)
    if skipped:
        print(f"  ! {path.name}: skipped {skipped} rows missing required fields")
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
                w.writerow(CSV_COLUMNS[fname])
                w.writerows(rows)
        print(f"Generated synthetic seed data in {data_dir}")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Tables created")

    db = SessionLocal()
    try:
        n_b = load_table(db, Bank, data_dir / "banks.csv", {}, [], ["bank_code", "bank_name"])
        n_a = load_table(db, Account, data_dir / "accounts.csv", {}, ["available_balance"],
                         ["account_id", "bank_code"])
        n_t = load_table(
            db, Transaction, data_dir / "transactions.csv",
            {"transaction_date": "dt"}, ["transaction_amount"], ["transaction_id", "account_id"],
        )
        db.commit()
        print(f"Loaded: {n_b} banks, {n_a} accounts, {n_t} transactions")
    except Exception as e:
        db.rollback()
        print(f"Load failed: {e}")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
