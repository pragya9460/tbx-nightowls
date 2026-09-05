"""Load TBX CSVs into MySQL (and optionally regenerate synthetic seed CSVs).

Usage:
    python scripts/load_data.py --data-dir ../data
    python scripts/load_data.py --generate --data-dir ../data
    python scripts/load_data.py --drop --database-url mysql://artha:artha@127.0.0.1:3306/artha
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
        "--database-url",
        default=os.environ.get("ARTHA_DATABASE_URL")
        or os.environ.get("ARTHA_MYSQL_URL"),
        help="mysql://user:pass@host:3306/db",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="truncate existing tables before load (default: yes when loading)",
    )
    parser.add_argument(
        "--no-drop",
        action="store_true",
        help="append without truncating (unsafe if PKs collide)",
    )
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

    from app.query_engine.mysql_store import load_csvs_into_mysql

    drop = not args.no_drop
    counts = load_csvs_into_mysql(
        data_dir=data_dir,
        url=args.database_url,
        drop=drop,
    )
    print(f"MySQL loaded: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
