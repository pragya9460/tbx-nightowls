"""DuckDB ingest + read-only connection helpers.

Loads the TBX CSV schema (banks, accounts, transactions) into a DuckDB file.
Chat-path queries open the file with ``read_only=True`` so the Text-to-SQL
executor cannot mutate data.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import duckdb

# Default path: repo ``data/finance.duckdb`` (override with ARTHA_DUCKDB_PATH).
_HERE = Path(__file__).resolve()
_CANDIDATE_ROOTS = [
    _HERE.parents[3],  # tbx-nightowls/ when path is backend/app/query_engine/
    _HERE.parents[2],  # backend/ (Docker WORKDIR=/app → /app/data)
    Path.cwd(),
]


def _resolve_default_data_dir() -> Path:
    for root in _CANDIDATE_ROOTS:
        candidate = root / "data"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "data"


_DEFAULT_DATA_DIR = _resolve_default_data_dir()
_DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "finance.duckdb"

# Physical table names. ``transaction`` is quoted — it is a DuckDB keyword.
TABLES = ("bank", "account", "transaction")

_CSV_TO_TABLE = {
    "banks.csv": "bank",
    "accounts.csv": "account",
    "transactions.csv": '"transaction"',
}


def default_data_dir() -> Path:
    return Path(os.environ.get("ARTHA_DATA_DIR", str(_DEFAULT_DATA_DIR))).resolve()


def default_duckdb_path() -> Path:
    return Path(os.environ.get("ARTHA_DUCKDB_PATH", str(_DEFAULT_DB_PATH))).resolve()


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute('DROP TABLE IF EXISTS "transaction"')
    con.execute("DROP TABLE IF EXISTS account")
    con.execute("DROP TABLE IF EXISTS bank")

    con.execute(
        """
        CREATE TABLE bank (
            bank_code VARCHAR PRIMARY KEY,
            bank_name VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE account (
            account_id VARCHAR PRIMARY KEY,
            entity_id VARCHAR NOT NULL,
            account_number VARCHAR NOT NULL,
            program_id INTEGER NOT NULL,
            available_balance DOUBLE NOT NULL,
            bank_code VARCHAR NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE "transaction" (
            transaction_id VARCHAR PRIMARY KEY,
            account_id VARCHAR NOT NULL,
            transaction_date TIMESTAMP NOT NULL,
            transaction_type VARCHAR NOT NULL,
            description VARCHAR,
            transaction_amount DOUBLE NOT NULL,
            transaction_reference_id VARCHAR,
            utr_number VARCHAR
        )
        """
    )
    con.execute("CREATE INDEX idx_account_bank ON account (bank_code)")
    con.execute('CREATE INDEX idx_txn_date ON "transaction" (transaction_date)')
    con.execute('CREATE INDEX idx_txn_account ON "transaction" (account_id)')
    con.execute('CREATE INDEX idx_txn_type ON "transaction" (transaction_type)')
    con.execute(
        'CREATE INDEX idx_txn_reference ON "transaction" (transaction_reference_id)'
    )


def build_duckdb_from_csvs(
    data_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> Path:
    """Create/replace ``finance.duckdb`` from CSV files in ``data_dir``."""
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    db_path = Path(db_path) if db_path else default_duckdb_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    con = duckdb.connect(str(db_path))
    try:
        _create_schema(con)
        for csv_name, table in _CSV_TO_TABLE.items():
            path = data_dir / csv_name
            if not path.exists():
                continue
            con.execute(
                f"""
                INSERT INTO {table}
                SELECT * FROM read_csv_auto(?, header=true, sample_size=-1)
                """,
                [str(path)],
            )
        con.commit()
    finally:
        con.close()
    return db_path


def build_duckdb_from_rows(
    banks: Iterable[dict[str, Any]],
    accounts: Iterable[dict[str, Any]],
    transactions: Iterable[dict[str, Any]],
    db_path: Path | str | None = None,
) -> duckdb.DuckDBPyConnection:
    """Build an in-memory (or file) DuckDB from row dicts — used by tests."""
    if db_path is None:
        con = duckdb.connect(":memory:")
    else:
        db_path = Path(db_path)
        if db_path.exists():
            db_path.unlink()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(db_path))

    _create_schema(con)

    def _insert(table: str, rows: Iterable[dict[str, Any]], cols: list[str]) -> None:
        rows = list(rows)
        if not rows:
            return
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        data = [tuple(r.get(c) if r.get(c) != "" else None for c in cols) for r in rows]
        con.executemany(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            data,
        )

    _insert("bank", banks, ["bank_code", "bank_name"])
    _insert(
        "account",
        accounts,
        [
            "account_id",
            "entity_id",
            "account_number",
            "program_id",
            "available_balance",
            "bank_code",
        ],
    )
    _insert(
        '"transaction"',
        transactions,
        [
            "transaction_id",
            "account_id",
            "transaction_date",
            "transaction_type",
            "description",
            "transaction_amount",
            "transaction_reference_id",
            "utr_number",
        ],
    )
    return con


def connect_readonly(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Open the finance DuckDB file for read-only query execution."""
    path = Path(db_path) if db_path else default_duckdb_path()
    if not path.exists():
        raise FileNotFoundError(
            f"DuckDB file not found at {path}. "
            "Run `python scripts/load_data.py --generate` (or build_duckdb_from_csvs) first."
        )
    return duckdb.connect(str(path), read_only=True)


def ensure_duckdb(data_dir: Path | str | None = None, db_path: Path | str | None = None) -> Path:
    """Build DuckDB from CSVs if missing; return path."""
    path = Path(db_path) if db_path else default_duckdb_path()
    if not path.exists():
        return build_duckdb_from_csvs(data_dir=data_dir, db_path=path)
    return path
