"""Settings: which .env file is read, and the defaults."""

from pathlib import Path

from sql_chat import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_load_env_reads_only_the_project_env_file(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: calls.append(args))
    config.load_env()
    assert calls == [(PROJECT_ROOT / ".env",)]


def test_defaults_without_settings():
    assert config.openai_model() == config.DEFAULT_MODEL
    assert not config.has_openai_key()
    defaults = config.db_defaults()
    assert (defaults.host, defaults.port, defaults.database) == ("127.0.0.1", "3306", "Chinook")
    assert defaults.user == defaults.password == ""
