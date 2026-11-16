from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from sql_chat.db import build_url, connect, describe_error, run_read_only


def test_build_url_escapes_special_characters():
    password = "p@ss:w/rd#%?&="
    url = build_url("chatbot_reader", password, "127.0.0.1", "3306", "Chinook")
    rendered = url.render_as_string(hide_password=False)

    assert password not in rendered
    parsed = make_url(rendered)
    assert parsed.password == password
    assert (parsed.username, parsed.host, parsed.port, parsed.database) == (
        "chatbot_reader",
        "127.0.0.1",
        3306,
        "Chinook",
    )
    assert password not in str(url)  # str() masks the password


def test_build_url_rejects_a_non_numeric_port():
    with pytest.raises(ValueError, match="Port must be a number"):
        build_url("user", "secret", "127.0.0.1", "33o6", "Chinook")


def test_run_read_only_returns_columns_and_rows(sqlite_engine):
    result = run_read_only(sqlite_engine, "SELECT ArtistId, Name FROM Artist ORDER BY ArtistId")

    assert result.columns == ["ArtistId", "Name"]
    assert result.rows[:2] == [(1, "AC/DC"), (2, "Accept")]
    assert len(result.rows) == 5
    assert result.truncated is False


def test_run_read_only_caps_rows(sqlite_engine):
    result = run_read_only(sqlite_engine, "SELECT Name FROM Artist ORDER BY ArtistId", max_rows=3)

    assert result.rows == [("AC/DC",), ("Accept",), ("Aerosmith",)]
    assert result.truncated is True


def test_run_read_only_at_exactly_the_cap_is_not_truncated(sqlite_engine):
    result = run_read_only(sqlite_engine, "SELECT Name FROM Artist", max_rows=5)

    assert len(result.rows) == 5
    assert result.truncated is False


def test_run_read_only_passes_percent_signs_and_colons_through(sqlite_engine):
    sql = "SELECT Name, 'a:b' AS label FROM Artist WHERE Name LIKE 'Al%' ORDER BY Name"
    result = run_read_only(sqlite_engine, sql)

    assert result.rows == [("Alanis Morissette", "a:b"), ("Alice In Chains", "a:b")]


def test_run_read_only_raises_database_errors(sqlite_engine):
    with pytest.raises(SQLAlchemyError, match="no such column"):
        run_read_only(sqlite_engine, "SELECT Nme FROM Artist")


class _FakeResult:
    returns_rows = True

    def keys(self):
        return ["n"]

    def fetchmany(self, size):
        return [(1,), (2,), (3,)][:size]


class _FakeConnection:
    def __init__(self):
        self.statements = []
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def exec_driver_sql(self, sql, *args, **kwargs):
        self.statements.append(sql)
        return _FakeResult()

    def rollback(self):
        self.rolled_back = True


def test_mysql_queries_run_in_a_read_only_transaction_with_limits():
    connection = _FakeConnection()
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=lambda: connection)

    result = run_read_only(engine, "SELECT n FROM t", max_rows=2, timeout_ms=5000)

    assert connection.statements == [
        "SET SESSION MAX_EXECUTION_TIME = 5000, SESSION sql_select_limit = 3",
        "START TRANSACTION READ ONLY",
        "SELECT n FROM t",
    ]
    assert connection.rolled_back
    assert result.rows == [(1,), (2,)]
    assert result.truncated is True


def test_connect_reads_the_schema_once(tmp_path):
    url = f"sqlite:///{(tmp_path / 'music.db').as_posix()}"
    setup = create_engine(url)
    with setup.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT)")
    setup.dispose()

    db = connect(url)
    try:
        assert "CREATE TABLE" in db.schema
        assert "Album" in db.schema
        assert run_read_only(db.engine, "SELECT COUNT(*) FROM Album").rows == [(0,)]
    finally:
        db.engine.dispose()


def test_describe_error_shows_the_driver_message(sqlite_engine):
    with pytest.raises(SQLAlchemyError) as caught:
        run_read_only(sqlite_engine, "SELECT Nme FROM Artist")

    assert describe_error(caught.value) == "OperationalError: no such column: Nme"


def test_describe_error_masks_the_password():
    exc = RuntimeError("login failed for 'reader' with password 'hunter2-secret'\nmore")

    message = describe_error(exc, secret="hunter2-secret")

    assert "hunter2-secret" not in message
    assert message == "RuntimeError: login failed for 'reader' with password '***' more"
