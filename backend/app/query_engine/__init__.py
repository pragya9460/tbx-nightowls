"""Query engine package — MySQL Text-to-SQL backend.

    from app.query_engine.mysql_engine import MySQLQueryEngine
    from app.query_engine.mysql_builder import compile_query
    from app.query_engine.result import QueryResult
"""

from .result import QueryResult  # noqa: F401

__all__ = [
    "QueryResult",
    "MySQLQueryEngine",
    "CompiledQuery",
    "compile_query",
]


def __getattr__(name: str):
    if name == "MySQLQueryEngine":
        from .mysql_engine import MySQLQueryEngine

        return MySQLQueryEngine
    if name in ("CompiledQuery", "compile_query"):
        from .mysql_builder import CompiledQuery, compile_query

        return {"CompiledQuery": CompiledQuery, "compile_query": compile_query}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name}")
