"""
Text-to-SQL analytics engine.

CSVs in data_dir are loaded into an in-memory SQLite database at startup.
Natural-language questions are converted to SQL by the LLM, executed against
SQLite, and the results are narrated back as a human-readable answer.
"""

import re
import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from langchain.prompts import ChatPromptTemplate
from langchain_anthropic import ChatAnthropic

from src.config import settings

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

SQL_GENERATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are an expert SQLite analyst working with financial data.
Given the database schema below and a natural-language question, write ONE valid SQLite SQL query that answers it.

Rules:
- Output ONLY the raw SQL — no markdown fences, no backticks, no explanation.
- Use correct SQLite syntax (use strftime() for date operations).
- transaction_amount is NEGATIVE for debits and POSITIVE for credits; use ABS() when computing spend.
- Always add LIMIT 100 unless you are writing a pure aggregation (GROUP BY) that naturally returns few rows.
- Prefer JOINs over sub-selects where readable.
- Column names are already lowercase with underscores — do not quote them.""",
        ),
        (
            "human",
            "Schema:\n{schema}\n\nQuestion: {question}\n\nSQL:",
        ),
    ]
)

ANSWER_FORMAT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a helpful financial analyst.
Given a question, the SQL that was executed, and its results, write a clear and concise natural-language answer.
- Lead with the direct answer.
- Highlight key numbers (use currency formatting where appropriate).
- If the result set is large, summarise the top findings and note the total row count.
- If results are empty, say so clearly and suggest a possible reason.""",
        ),
        (
            "human",
            "Question: {question}\n\nSQL executed:\n{sql}\n\nQuery results:\n{results}\n\nAnswer:",
        ),
    ]
)


# ── Engine ────────────────────────────────────────────────────────────────────


class DataAnalytics:
    """
    Loads every CSV under data_dir into an in-memory SQLite database and
    answers aggregation questions via Text-to-SQL.
    """

    def __init__(self) -> None:
        self.llm = ChatAnthropic(
            anthropic_api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=0,
        )
        self._conn: Optional[sqlite3.Connection] = None
        self._schema: str = ""
        self._tables: List[str] = []

    # ── Internal setup ────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Return (and lazily create) the SQLite connection."""
        if self._conn is None:
            # check_same_thread=False is safe here because FastAPI serialises
            # route handlers and we never do concurrent writes.
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._load_csvs()
        return self._conn

    # SQLite reserved words that cannot be used as bare table/column names.
    _SQLITE_RESERVED = {
        "transaction",
        "table",
        "index",
        "select",
        "from",
        "where",
        "group",
        "order",
        "by",
        "join",
        "left",
        "right",
        "inner",
        "create",
        "drop",
        "insert",
        "update",
        "delete",
        "values",
        "primary",
        "key",
        "foreign",
        "references",
        "default",
        "null",
    }

    def _sanitise_name(self, name: str) -> str:
        """Convert a filename/column name to a safe SQLite identifier."""
        clean = re.sub(r"[^a-z0-9_]", "_", name.strip().lower())
        # Prefix reserved words so they can be used as bare identifiers.
        if clean in self._SQLITE_RESERVED:
            clean = f"tbl_{clean}"
        return clean

    def _load_csvs(self) -> None:
        """Read every CSV under data_dir and store it as a SQLite table."""
        data_dir = settings.data_dir
        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return

        for csv_path in sorted(data_dir.rglob("*.csv")):
            table_name = self._sanitise_name(csv_path.stem)
            try:
                df = pd.read_csv(csv_path)
                # Normalise column names so SQL is predictable
                df.columns = [self._sanitise_name(c) for c in df.columns]
                df.to_sql(table_name, self._conn, if_exists="replace", index=False)
                self._tables.append(table_name)
                logger.info(
                    f"Loaded '{csv_path.name}' → table '{table_name}' ({len(df):,} rows)"
                )
            except Exception as exc:
                logger.error(f"Failed to load {csv_path.name}: {exc}")

        if self._tables:
            self._schema = self._build_schema()
            logger.info(f"Analytics DB ready — tables: {self._tables}")
        else:
            logger.warning("No CSV files found; analytics engine has no data.")

    def _build_schema(self) -> str:
        """Return a human-readable schema string for all loaded tables."""
        conn = self._conn
        parts: List[str] = []

        for table in self._tables:
            # Column definitions
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            col_defs = ", ".join(f"{c[1]} ({c[2]})" for c in cols)

            # Row count
            (row_count,) = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()

            # 3-row sample
            col_names = [c[1] for c in cols]
            sample_rows = conn.execute(f'SELECT * FROM "{table}" LIMIT 3').fetchall()
            sample_lines = "\n".join(
                "  " + "  |  ".join(f"{k}={v}" for k, v in zip(col_names, row))
                for row in sample_rows
            )

            parts.append(
                f"TABLE: {table}  ({row_count:,} rows)\n"
                f"COLUMNS: {col_defs}\n"
                f"SAMPLE:\n{sample_lines}"
            )

        return "\n\n".join(parts)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_schema(self) -> str:
        """Return the schema string, triggering lazy DB init if needed."""
        self._get_conn()
        return self._schema

    def get_tables(self) -> List[str]:
        self._get_conn()
        return list(self._tables)

    def generate_sql(self, question: str) -> str:
        """Ask the LLM to turn *question* into a SQLite SQL query."""
        msgs = SQL_GENERATION_PROMPT.format_messages(
            schema=self.get_schema(),
            question=question,
        )
        raw = self.llm.invoke(msgs).content.strip()
        # Strip accidental markdown code fences
        raw = re.sub(r"^```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
        return raw

    def execute_sql(self, sql: str) -> Tuple[List[str], List[tuple]]:
        """Execute *sql* and return (column_names, rows)."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            return columns, rows
        except sqlite3.Error as exc:
            logger.error(f"SQL execution failed:\n{sql}\nError: {exc}")
            raise ValueError(f"SQL execution error: {exc}") from exc

    def _results_to_string(self, columns: List[str], rows: List[tuple]) -> str:
        """Format query results as a readable table string (max 50 preview rows)."""
        if not rows:
            return "(no rows returned)"
        df = pd.DataFrame(rows[:50], columns=columns)
        result = df.to_string(index=False)
        if len(rows) > 50:
            result += f"\n... ({len(rows)} total rows, showing first 50)"
        return result

    def answer_question(self, question: str) -> Dict[str, Any]:
        """
        Full pipeline:
          1. Generate SQL from the question.
          2. Execute SQL against SQLite.
          3. Ask the LLM to narrate the results.

        Returns a dict with keys: answer, sql, row_count, data.
        """
        # Step 1 – generate SQL
        sql = self.generate_sql(question)
        logger.info(f"Generated SQL:\n{sql}")

        # Step 2 – execute
        columns, rows = self.execute_sql(sql)
        results_str = self._results_to_string(columns, rows)
        logger.info(f"SQL returned {len(rows)} row(s)")

        # Step 3 – narrate
        msgs = ANSWER_FORMAT_PROMPT.format_messages(
            question=question,
            sql=sql,
            results=results_str,
        )
        answer = self.llm.invoke(msgs).content

        return {
            "answer": answer,
            "sql": sql,
            "row_count": len(rows),
            # Cap raw data at 100 rows so the API response stays manageable
            "data": [dict(zip(columns, row)) for row in rows[:100]],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_analytics_instance: Optional[DataAnalytics] = None


def get_analytics() -> DataAnalytics:
    """Return (and lazily create) the module-level DataAnalytics singleton."""
    global _analytics_instance
    if _analytics_instance is None:
        _analytics_instance = DataAnalytics()
    return _analytics_instance
