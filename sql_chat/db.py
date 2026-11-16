"""Connecting to the database and running read-only queries."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_community.utilities import SQLDatabase
from mysql.connector.constants import ClientFlag
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url

MAX_ROWS = 200
MAX_EXECUTION_MS = 15_000
# mysql-connector turns multi-statement support on by default, so "SELECT 1; DELETE ..."
# would run both statements. These flags turn it off, and the server then refuses such a
# batch as a syntax error. FOUND_ROWS is the flag SQLAlchemy adds, kept as it would be.
MYSQL_CLIENT_FLAGS = (ClientFlag.FOUND_ROWS, -ClientFlag.MULTI_STATEMENTS)


@dataclass
class Database:
    """A connection pool and the schema text the model sees (read once, at connect time)."""

    engine: Engine
    schema: str


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool  # True when the query returned more rows than were kept


def build_url(user: str, password: str, host: str, port: str | int, database: str) -> URL:
    """Build a MySQL connection URL. URL.create escapes special characters in every part."""
    port_text = str(port).strip()
    if port_text and not port_text.isdigit():
        raise ValueError(f"Port must be a number, got {port_text!r}.")
    return URL.create(
        "mysql+mysqlconnector",
        username=user or None,
        password=password or None,
        host=host.strip() or None,
        port=int(port_text) if port_text else None,
        database=database.strip() or None,
    )


def make_engine(url: URL | str) -> Engine:
    """Create the connection pool. No connection is opened until it is used."""
    if make_url(url).get_backend_name() == "mysql":
        # connect_args replace the client_flags SQLAlchemy computes for mysql-connector.
        connect_args = {"client_flags": MYSQL_CLIENT_FLAGS}
        return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return create_engine(url, pool_pre_ping=True)


def connect(url: URL | str) -> Database:
    """Open a connection pool, check that it works and read the schema once."""
    engine = make_engine(url)
    try:
        schema = SQLDatabase(engine).get_table_info()
    except Exception:
        engine.dispose()
        raise
    return Database(engine=engine, schema=schema)


def run_read_only(
    engine: Engine, sql: str, max_rows: int = MAX_ROWS, timeout_ms: int = MAX_EXECUTION_MS
) -> QueryResult:
    """Run one query and return its columns and at most max_rows rows.

    On MySQL the query runs in a READ ONLY transaction and is stopped after timeout_ms.
    sql_select_limit caps a query that has no LIMIT of its own at max_rows + 1 rows (the
    extra row only tells us there were more). A query with its own larger LIMIT is not
    capped by the server, and SQLAlchemy's mysql-connector dialect buffers every row it
    sends; only the first max_rows + 1 are read from that buffer. The transaction is
    always rolled back. Database errors propagate.
    """
    with engine.connect() as conn:
        if engine.dialect.name == "mysql":
            conn.exec_driver_sql(
                f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}, "
                f"SESSION sql_select_limit = {int(max_rows) + 1}"
            )
            conn.exec_driver_sql("START TRANSACTION READ ONLY")
        try:
            # no_parameters: hand the SQL to the driver untouched (no %-placeholder parsing).
            result = conn.exec_driver_sql(sql, execution_options={"no_parameters": True})
            if result.returns_rows:
                columns = list(result.keys())
                rows = [tuple(row) for row in result.fetchmany(max_rows + 1)]
            else:
                columns, rows = [], []
        finally:
            conn.rollback()
    return QueryResult(columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows)


def describe_error(exc: BaseException, secret: str = "") -> str:
    """One line for the user: the error class and the driver's message, with secret masked."""
    detail = getattr(exc, "orig", None) or exc
    message = " ".join(str(detail).split())
    if secret:
        message = message.replace(secret, "***")
    if len(message) > 300:
        message = message[:297] + "..."
    return f"{type(exc).__name__}: {message}"
