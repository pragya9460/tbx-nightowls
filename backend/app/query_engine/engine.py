"""Query engine facade — runtime uses DuckDB only.

Kept so older imports of ``QueryResult`` and masking helpers keep working.
Prefer::

    from app.query_engine.duckdb_engine import DuckDBQueryEngine
    from app.query_engine.result import QueryResult
"""
from .duckdb_engine import DuckDBQueryEngine, mask_account_number, mask_utr
from .result import QueryResult

__all__ = [
    "QueryResult",
    "DuckDBQueryEngine",
    "FinancialQueryEngine",
    "mask_account_number",
    "mask_utr",
]

# Historical name from the SQLAlchemy engine — tests/imports still use it.
FinancialQueryEngine = DuckDBQueryEngine
