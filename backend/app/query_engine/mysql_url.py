"""Parse and mask MySQL connection URLs."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class MySQLConnectParams:
    host: str
    port: int
    user: str
    password: str
    database: str


def normalize_mysql_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("database URL is empty")
    if raw.startswith("mysql+pymysql://"):
        return "mysql://" + raw[len("mysql+pymysql://") :]
    if raw.startswith("mysql://"):
        return raw
    raise ValueError("only mysql:// or mysql+pymysql:// URLs are supported")


def parse_mysql_url(url: str) -> MySQLConnectParams:
    normalized = normalize_mysql_url(url)
    parsed = urlparse(normalized)
    if parsed.scheme != "mysql":
        raise ValueError("only mysql:// URLs are supported")
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("database name is required in the URL path")
    if not parsed.hostname:
        raise ValueError("host is required in the database URL")
    return MySQLConnectParams(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or "root"),
        password=unquote(parsed.password or ""),
        database=database,
    )


def mask_mysql_url(url: str) -> str:
    """Return a display-safe URL with the password redacted."""
    try:
        normalized = normalize_mysql_url(url)
        parsed = urlparse(normalized)
        user = unquote(parsed.username or "")
        host = parsed.hostname or "localhost"
        port = parsed.port or 3306
        database = (parsed.path or "").lstrip("/")
        auth = f"{user}:***" if user else "***"
        return f"mysql://{auth}@{host}:{port}/{database}"
    except Exception:
        return "mysql://***"
