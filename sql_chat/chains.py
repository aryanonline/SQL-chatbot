"""The two model steps: question -> SQL, then SQL result -> plain-English answer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, get_buffer_string
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from sqlalchemy.exc import SQLAlchemyError

from sql_chat import config
from sql_chat.db import MAX_ROWS, Database, QueryResult, describe_error, run_read_only
from sql_chat.guard import clean_sql, is_read_only

HISTORY_MESSAGES = 10  # only the most recent chat messages are sent to the model
MAX_CELL_CHARS = 200

SQL_PROMPT = ChatPromptTemplate.from_template(
    """You are a data analyst. Write a MySQL query that answers the user's question, \
based on the table schema below. Take the conversation history into account.

<SCHEMA>
{schema}
</SCHEMA>

Conversation history:
{history}

Write only the SQL query and nothing else: no explanation, and do not wrap the query \
in markdown or backticks. The query must only read data: one SELECT statement.

For example:
Question: Which 3 artists have the most tracks?
SQL Query: SELECT Artist.Name, COUNT(*) AS track_count FROM Track \
JOIN Album ON Track.AlbumId = Album.AlbumId JOIN Artist ON Album.ArtistId = Artist.ArtistId \
GROUP BY Artist.ArtistId, Artist.Name ORDER BY track_count DESC LIMIT 3;
Question: Name 10 artists
SQL Query: SELECT Name FROM Artist LIMIT 10;
{retry}
Your turn:
Question: {question}
SQL Query:"""
)

RETRY_NOTE = """
Your previous query for this question failed.
Query: {sql}
Error: {error}
Write a corrected query.
"""

ANSWER_PROMPT = ChatPromptTemplate.from_template(
    """You are a data analyst. Answer the user's question in plain English, based on \
the SQL query and its result below. Take the conversation history into account. Use only \
the data in the result; if the result has no rows, say that nothing matched.

Conversation history:
{history}

Question: {question}
SQL query: {sql}
Result ({summary}):
{result}"""
)


@dataclass
class ChatResult:
    """Everything the UI shows for one question."""

    answer: str
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    error: str = ""  # why the query was rejected or failed, if it was


class _Rejected(Exception):
    """The read-only guard refused the SQL."""


def make_llm() -> ChatOpenAI:
    """The OpenAI chat model. The API key is read from OPENAI_API_KEY."""
    return ChatOpenAI(model=config.openai_model())


def get_response(
    question: str,
    history: Sequence[BaseMessage],
    db: Database,
    llm: BaseChatModel,
    max_rows: int = MAX_ROWS,
) -> ChatResult:
    """Answer one question: write SQL, check it, run it, and explain the result.

    If the database rejects the query, the model gets one more try with the error text.
    A query the guard rejects is never run and is not retried.
    """
    inputs = {
        "schema": db.schema,
        "history": format_history(history),
        "question": question,
        "retry": "",
    }
    write_sql = SQL_PROMPT | llm | StrOutputParser()

    sql = clean_sql(write_sql.invoke(inputs))
    try:
        try:
            result = _check_and_run(db, sql, max_rows)
        except SQLAlchemyError as exc:
            # The one retry: show the model its query and the database's error message.
            inputs["retry"] = RETRY_NOTE.format(sql=sql, error=describe_error(exc))
            sql = clean_sql(write_sql.invoke(inputs))
            result = _check_and_run(db, sql, max_rows)
    # Either attempt can be rejected by the guard; a second database error is final.
    except _Rejected as rejected:
        reason = str(rejected)
        return ChatResult(
            answer=f"I did not run the generated SQL. It failed the read-only check: {reason}",
            sql=sql,
            error=reason,
        )
    except SQLAlchemyError as exc:
        error = describe_error(exc)
        return ChatResult(
            answer=f"Sorry, I could not write a query that runs. The database said: {error}",
            sql=sql,
            error=error,
        )

    answer = (ANSWER_PROMPT | llm | StrOutputParser()).invoke(
        {
            "history": inputs["history"],
            "question": question,
            "sql": sql,
            "summary": _summary(result),
            "result": format_result(result),
        }
    )
    return ChatResult(
        answer=answer.strip(),
        sql=sql,
        columns=result.columns,
        rows=result.rows,
        truncated=result.truncated,
    )


def format_history(messages: Sequence[BaseMessage], limit: int = HISTORY_MESSAGES) -> str:
    recent = list(messages)[-limit:] if limit > 0 else []
    if not recent:
        return "(none)"
    return get_buffer_string(recent, human_prefix="User", ai_prefix="Assistant")


def format_result(result: QueryResult) -> str:
    if not result.columns:
        return "(the query returned no result set)"
    lines = [" | ".join(result.columns)]
    lines += [" | ".join(_cell(value) for value in row) for row in result.rows]
    return "\n".join(lines)


def _check_and_run(db: Database, sql: str, max_rows: int) -> QueryResult:
    ok, reason = is_read_only(sql)
    if not ok:
        raise _Rejected(reason)
    return run_read_only(db.engine, sql, max_rows=max_rows)


def _summary(result: QueryResult) -> str:
    count = len(result.rows)
    if result.truncated:
        return f"only the first {count} rows are shown; the query returned more"
    return f"{count} row" if count == 1 else f"{count} rows"


def _cell(value: object) -> str:
    text = "NULL" if value is None else str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 3] + "..."
