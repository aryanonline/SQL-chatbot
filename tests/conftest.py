"""Shared fixtures. Tests use in-memory SQLite and fake models: no MySQL, no OpenAI, no network."""

import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from sql_chat import config

SETTINGS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
)
ARTISTS = ["AC/DC", "Accept", "Aerosmith", "Alanis Morissette", "Alice In Chains"]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Ignore any local .env file and settings exported in the developer's shell."""
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any connection to a non-local address fail the test."""
    real_connect = socket.socket.connect

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"tests must not use the network (tried to connect to {address})")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def sqlite_engine():
    """An in-memory SQLite database with a small Artist table (ArtistId 1-5)."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name TEXT)")
        for artist_id, name in enumerate(ARTISTS, start=1):
            conn.exec_driver_sql("INSERT INTO Artist VALUES (?, ?)", (artist_id, name))
    yield engine
    engine.dispose()
