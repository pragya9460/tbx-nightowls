"""Query engine package — DuckDB Text-to-SQL backend.

    from app.query_engine.duckdb_engine import DuckDBQueryEngine
    from app.query_engine.duckdb_builder import compile_query
    from app.query_engine.result import QueryResult
"""

from .result import QueryResult  # noqa: F401

__all__ = [
    "QueryResult",
    "DuckDBQueryEngine",
    "CompiledQuery",
    "compile_query",
]


def __getattr__(name: str):
    if name == "DuckDBQueryEngine":
        from .duckdb_engine import DuckDBQueryEngine

        return DuckDBQueryEngine
    if name in ("CompiledQuery", "compile_query"):
        from .duckdb_builder import CompiledQuery, compile_query

        return {"CompiledQuery": CompiledQuery, "compile_query": compile_query}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
