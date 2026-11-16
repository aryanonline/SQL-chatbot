import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, get_buffer_string

from sql_chat import chains
from sql_chat.chains import format_history, get_response, make_llm
from sql_chat.db import Database

SCHEMA = "CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name TEXT)"


class PromptRecorder(BaseCallbackHandler):
    """Keeps the text of every prompt sent to the fake model."""

    def __init__(self):
        self.prompts = []

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.prompts.append(get_buffer_string(messages[0]))


def fake_model(*responses):
    """A fake chat model that replies with `responses` in order, and its prompt recorder."""
    recorder = PromptRecorder()
    return FakeListChatModel(responses=list(responses), callbacks=[recorder]), recorder.prompts


@pytest.fixture
def db(sqlite_engine):
    return Database(engine=sqlite_engine, schema=SCHEMA)


def test_happy_path(db):
    llm, prompts = fake_model(
        "```sql\nSELECT Name FROM Artist ORDER BY ArtistId LIMIT 3;\n```",
        "The first three artists are AC/DC, Accept and Aerosmith.",
    )

    result = get_response("Name three artists", [], db, llm)

    assert result.sql == "SELECT Name FROM Artist ORDER BY ArtistId LIMIT 3"
    assert result.columns == ["Name"]
    assert result.rows == [("AC/DC",), ("Accept",), ("Aerosmith",)]
    assert result.truncated is False
    assert result.answer == "The first three artists are AC/DC, Accept and Aerosmith."
    assert result.error == ""
    assert len(prompts) == 2
    assert SCHEMA in prompts[0]
    assert "Name three artists" in prompts[0]
    assert "SELECT Name FROM Artist ORDER BY ArtistId LIMIT 3" in prompts[1]
    assert "Aerosmith" in prompts[1]
    assert "3 rows" in prompts[1]


def test_retries_once_after_a_database_error(db):
    llm, prompts = fake_model(
        "SELECT Nme FROM Artist",
        "SELECT Name FROM Artist ORDER BY ArtistId",
        "There are five artists.",
    )

    result = get_response("List the artists", [], db, llm)

    assert result.sql == "SELECT Name FROM Artist ORDER BY ArtistId"
    assert len(result.rows) == 5
    assert result.error == ""
    assert result.answer == "There are five artists."
    assert len(prompts) == 3
    assert "previous query for this question failed" not in prompts[0]
    assert "Query: SELECT Nme FROM Artist" in prompts[1]
    assert "no such column: Nme" in prompts[1]


def test_gives_up_after_a_second_database_error(db):
    llm, prompts = fake_model("SELECT Nme FROM Artist", "SELECT Nmee FROM Artist")

    result = get_response("List the artists", [], db, llm)

    assert result.sql == "SELECT Nmee FROM Artist"
    assert "no such column: Nmee" in result.error
    assert result.error in result.answer
    assert result.rows == []
    assert len(prompts) == 2  # no answer step


def test_guard_rejection_never_runs_the_query(db, monkeypatch):
    executed = []
    monkeypatch.setattr(chains, "run_read_only", lambda *args, **kwargs: executed.append(args))
    llm, prompts = fake_model("DELETE FROM Artist")

    result = get_response("Delete every artist", [], db, llm)

    assert executed == []
    assert result.sql == "DELETE FROM Artist"
    assert result.error == "Only SELECT queries are allowed."
    assert "read-only check" in result.answer
    assert result.rows == []
    assert len(prompts) == 1  # no retry, no answer step


def test_rejected_retry_is_not_run(db):
    llm, prompts = fake_model("SELECT Nme FROM Artist", "SELECT SLEEP(100)")

    result = get_response("List the artists", [], db, llm)

    assert result.sql == "SELECT SLEEP(100)"
    assert "SLEEP" in result.error
    assert len(prompts) == 2


def test_only_recent_history_is_sent(db):
    history = [
        HumanMessage(f"question-{i:02d}") if i % 2 == 0 else AIMessage(f"answer-{i:02d}")
        for i in range(30)
    ]
    llm, prompts = fake_model("SELECT Name FROM Artist", "Five artists.")

    get_response("And now?", history, db, llm)

    for prompt in prompts:  # both the SQL step and the answer step
        assert "User: question-20" in prompt
        assert "Assistant: answer-29" in prompt
        assert "answer-19" not in prompt
        assert "question-00" not in prompt


def test_answer_step_is_told_when_rows_were_cut(db):
    llm, prompts = fake_model("SELECT Name FROM Artist ORDER BY ArtistId", "At least two.")

    result = get_response("List the artists", [], db, llm, max_rows=2)

    assert result.rows == [("AC/DC",), ("Accept",)]
    assert result.truncated is True
    assert "only the first 2 rows are shown" in prompts[1]
    assert "Aerosmith" not in prompts[1]


def test_format_history_without_messages():
    assert format_history([]) == "(none)"


def test_make_llm_model_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert make_llm().model_name == "gpt-5.6-terra"
    assert make_llm().temperature is None  # GPT-5 models only accept the default

    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")
    assert make_llm().model_name == "gpt-test-model"
