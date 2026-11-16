"""Tests for the Streamlit UI. No MySQL and no OpenAI: SQLite and a fake model stand in."""

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from streamlit.testing.v1 import AppTest

from sql_chat import chains, db
from sql_chat.db import Database

APP = Path(__file__).resolve().parents[1] / "app.py"


def run_app():
    return AppTest.from_file(APP, default_timeout=60).run()


def test_app_loads(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    at = run_app()

    assert not at.exception
    assert at.title[0].value == "Chat with MySQL"
    fields = {field.label: field.value for field in at.sidebar.text_input}
    assert fields == {
        "Host": "127.0.0.1",
        "Port": "3306",
        "User": "",
        "Password": "",
        "Database": "Chinook",
    }


def test_db_password_is_used_but_not_sent_to_the_browser(monkeypatch, sqlite_engine):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("DB_PASSWORD", "env-db-password")
    urls = []

    def fake_connect(url):
        urls.append(url)
        return Database(sqlite_engine, schema="Artist")

    monkeypatch.setattr(db, "connect", fake_connect)
    at = run_app()

    assert [field.value for field in at.sidebar.text_input if field.label == "Password"] == [""]
    assert "env-db-password" not in [field.value for field in at.sidebar.text_input]

    at.sidebar.button[0].click().run()

    assert not at.exception
    assert urls[0].password == "env-db-password"


def test_chat_before_connecting_asks_to_connect_first(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    at = run_app()

    at.chat_input[0].set_value("How many artists are there?").run()

    assert not at.exception
    assert [info.value for info in at.info] == ["Connect to a database in the sidebar first."]
    assert at.session_state.messages == []


def test_missing_api_key_shows_a_message():
    at = run_app()

    assert not at.exception
    assert "OPENAI_API_KEY is not set" in at.error[0].value
    assert len(at.chat_input) == 0


def test_connected_chat_shows_answer_sql_and_rows(monkeypatch, sqlite_engine):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(db, "connect", lambda url: Database(sqlite_engine, schema="Artist"))
    monkeypatch.setattr(
        chains,
        "make_llm",
        lambda: FakeListChatModel(
            responses=["SELECT Name FROM Artist ORDER BY ArtistId LIMIT 2", "AC/DC, $1 each."]
        ),
    )
    at = run_app()
    at.sidebar.button[0].click().run()
    assert at.sidebar.success[0].value == "Connected to Chinook on 127.0.0.1:3306."

    at.chat_input[0].set_value("Name two artists").run()
    at.run()  # a later rerun redraws the conversation from session state

    assert not at.exception
    assert at.chat_message[1].markdown[0].value == "Name two artists"
    assert at.chat_message[2].markdown[0].value == "AC/DC, \\$1 each."  # "$" escaped
    assert at.expander[0].label == "SQL and result"
    assert at.code[0].value == "SELECT Name FROM Artist ORDER BY ArtistId LIMIT 2"
    assert at.dataframe[0].value["Name"].tolist() == ["AC/DC", "Accept"]
