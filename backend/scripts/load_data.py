"""Build DuckDB from CSVs (and optionally regenerate synthetic seed CSVs).

Works for BOTH the synthetic seed and the official TBX dataset, as long as
files share the documented column names (see app/services/seed_data.py
CSV_COLUMNS).

Usage:
    python scripts/load_data.py --data-dir ../data
    python scripts/load_data.py --generate --data-dir ../data
    python scripts/load_data.py --generate --drop             # DuckDB is rebuilt
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ARTHA_DATA_DIR", "../data"),
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="generate synthetic seed CSVs into data-dir first",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--duckdb-path",
        default=os.environ.get("ARTHA_DUCKDB_PATH"),
        help="override path for finance.duckdb",
    )
    # Compatibility no-ops from the old MySQL/Postgres CLI.
    parser.add_argument("--drop", action="store_true", help="(ignored; DuckDB is rebuilt)")
    parser.add_argument("--duckdb", action="store_true", help="(ignored; always builds DuckDB)")
    parser.add_argument("--duckdb-only", action="store_true", help="(ignored; always DuckDB-only)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()

    if args.generate:
        from app.services.seed_data import CSV_COLUMNS, generate, to_csv_rows

        bundle = generate(seed=args.seed)
        data_dir.mkdir(parents=True, exist_ok=True)
        for fname, rows in to_csv_rows(bundle).items():
            with open(data_dir / fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(CSV_COLUMNS[fname])
                w.writerows(rows)
        print(f"Generated synthetic seed data in {data_dir}")

    from app.query_engine.duckdb_store import build_duckdb_from_csvs

    db_path = Path(args.duckdb_path).resolve() if args.duckdb_path else None
    path = build_duckdb_from_csvs(data_dir=data_dir, db_path=db_path)
    print(f"DuckDB built at {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
