"""MySQL schema bootstrap + CSV / row loaders for the TBX dataset."""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Iterable

import pymysql
from pymysql.connections import Connection

from .mysql_url import parse_mysql_url

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"

_BANK_COLS = ("bank_code", "bank_name")
_ACCOUNT_COLS = (
    "account_id",
    "entity_id",
    "account_number",
    "program_id",
    "available_balance",
    "bank_code",
)
_TXN_COLS = (
    "transaction_id",
    "account_id",
    "transaction_date",
    "transaction_type",
    "description",
    "transaction_amount",
    "transaction_reference_id",
    "utr_number",
)


def default_data_dir() -> Path:
    override = os.environ.get("ARTHA_DATA_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return _DEFAULT_DATA_DIR


def default_database_url() -> str:
    return os.environ.get(
        "ARTHA_DATABASE_URL",
        "mysql://artha:artha@127.0.0.1:3306/artha",
    ).strip()


def connect(url: str | None = None, *, autocommit: bool = True) -> Connection:
    params = parse_mysql_url(url or default_database_url())
    return pymysql.connect(
        host=params.host,
        port=params.port,
        user=params.user,
        password=params.password,
        database=params.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


def apply_schema(con: Connection) -> None:
    ddl = _SCHEMA_PATH.read_text()
    with con.cursor() as cur:
        for stmt in _split_sql(ddl):
            cur.execute(stmt)


def _split_sql(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            parts.append("\n".join(buf).rstrip(";").strip())
            buf = []
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


def truncate_all(con: Connection) -> None:
    with con.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        cur.execute("TRUNCATE TABLE `transaction`")
        cur.execute("TRUNCATE TABLE account")
        cur.execute("TRUNCATE TABLE bank")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")


def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def insert_banks(con: Connection, rows: Iterable[dict]) -> int:
    sql = "INSERT INTO bank (bank_code, bank_name) VALUES (%s, %s)"
    data = [(r["bank_code"], r["bank_name"]) for r in rows]
    if not data:
        return 0
    with con.cursor() as cur:
        cur.executemany(sql, data)
    return len(data)


def insert_accounts(con: Connection, rows: Iterable[dict]) -> int:
    sql = (
        "INSERT INTO account "
        "(account_id, entity_id, account_number, program_id, available_balance, bank_code) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    data = []
    for r in rows:
        data.append(
            (
                r["account_id"],
                r["entity_id"],
                r["account_number"],
                int(r["program_id"]),
                float(r["available_balance"]),
                r["bank_code"],
            )
        )
    if not data:
        return 0
    with con.cursor() as cur:
        cur.executemany(sql, data)
    return len(data)


def insert_transactions(con: Connection, rows: Iterable[dict], *, batch: int = 500) -> int:
    sql = (
        "INSERT INTO `transaction` "
        "(transaction_id, account_id, transaction_date, transaction_type, description, "
        "transaction_amount, transaction_reference_id, utr_number) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    total = 0
    buf: list[tuple] = []
    with con.cursor() as cur:
        for r in rows:
            buf.append(
                (
                    r["transaction_id"],
                    r["account_id"],
                    r["transaction_date"],
                    r["transaction_type"],
                    _empty_to_none(r.get("description")),
                    float(r["transaction_amount"]),
                    _empty_to_none(r.get("transaction_reference_id")),
                    _empty_to_none(r.get("utr_number")),
                )
            )
            if len(buf) >= batch:
                cur.executemany(sql, buf)
                total += len(buf)
                buf = []
        if buf:
            cur.executemany(sql, buf)
            total += len(buf)
    return total


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_csvs_into_mysql(
    *,
    data_dir: Path | str | None = None,
    url: str | None = None,
    drop: bool = True,
) -> dict[str, int]:
    """Create schema and load banks/accounts/transactions CSVs."""
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    banks_path = data_dir / "banks.csv"
    accounts_path = data_dir / "accounts.csv"
    txns_path = data_dir / "transactions.csv"
    for p in (banks_path, accounts_path, txns_path):
        if not p.exists():
            raise FileNotFoundError(f"missing CSV: {p}")

    con = connect(url, autocommit=False)
    try:
        apply_schema(con)
        if drop:
            truncate_all(con)
        counts = {
            "bank": insert_banks(con, _read_csv(banks_path)),
            "account": insert_accounts(con, _read_csv(accounts_path)),
            "transaction": insert_transactions(con, _read_csv(txns_path)),
        }
        con.commit()
        return counts
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def load_rows_into_mysql(
    banks: list[dict],
    accounts: list[dict],
    transactions: list[dict],
    *,
    url: str | None = None,
    drop: bool = True,
) -> dict[str, int]:
    """Load in-memory seed rows (tests)."""
    con = connect(url, autocommit=False)
    try:
        apply_schema(con)
        if drop:
            truncate_all(con)
        counts = {
            "bank": insert_banks(con, banks),
            "account": insert_accounts(con, accounts),
            "transaction": insert_transactions(con, transactions),
        }
        con.commit()
        return counts
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
