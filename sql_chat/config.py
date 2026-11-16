"""Settings from environment variables, optionally loaded from a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-5.6-terra"
# The project's own .env. Without an explicit path, python-dotenv would also search the
# parent folders and could load an unrelated .env (with a different API key, say).
PROJECT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class DbDefaults:
    """Initial values for the connection form in the sidebar."""

    host: str
    port: str
    user: str
    password: str
    database: str


def load_env() -> None:
    """Load the project's .env file if there is one. Variables already set in the
    environment win."""
    load_dotenv(PROJECT_ENV_FILE)


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


def has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def db_defaults() -> DbDefaults:
    return DbDefaults(
        host=os.getenv("DB_HOST") or "127.0.0.1",
        port=os.getenv("DB_PORT") or "3306",
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME") or "Chinook",
    )
