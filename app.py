"""Streamlit chat UI for asking a MySQL database questions. Run: streamlit run app.py"""

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy.exc import SQLAlchemyError

from sql_chat import config
from sql_chat.chains import ChatResult, get_response, make_llm
from sql_chat.db import build_url, connect, describe_error

GREETING = (
    "Hello! I'm your SQL assistant. Connect to a database in the sidebar, "
    "then ask me anything about it."
)

st.set_page_config(page_title="Chat with MySQL", page_icon=":speech_balloon:", layout="wide")
config.load_env()
st.title("Chat with MySQL")

if not config.has_openai_key():
    st.error(
        "OPENAI_API_KEY is not set. Copy .env.example to .env, add your OpenAI API key, "
        "then restart the app."
    )
    st.stop()

if "messages" not in st.session_state:
    # Each message: {"role", "content"}; assistant messages also keep "result" (a ChatResult).
    st.session_state.messages = []


def escape_dollars(text: str) -> str:
    """Keep Streamlit's markdown from reading "$...$" (prices, for example) as LaTeX."""
    return text.replace("$", "\\$")


def as_records(columns: list[str], rows: list[tuple]) -> list[dict]:
    """Rows as dicts for st.dataframe, renaming repeated column names (Name, Name (2), ...)."""
    names: list[str] = []
    for column in columns:
        name, n = column, 2
        while name in names:
            name, n = f"{column} ({n})", n + 1
        names.append(name)
    return [dict(zip(names, row, strict=True)) for row in rows]


def show_details(result: ChatResult) -> None:
    """The "SQL and result" expander under an answer."""
    if not result.sql:
        return
    with st.expander("SQL and result"):
        st.code(result.sql, language="sql")
        if result.error:
            st.caption(result.error)
        elif not result.rows:
            st.caption("The query returned no rows.")
        else:
            st.dataframe(as_records(result.columns, result.rows), hide_index=True)
            if result.truncated:
                st.caption(f"Showing the first {len(result.rows)} rows; the query returned more.")


def connection_sidebar() -> None:
    defaults = config.db_defaults()
    with st.sidebar:
        st.subheader("Database")
        st.write("Connect with a MySQL user that can only read data.")
        host = st.text_input("Host", value=defaults.host)
        port = st.text_input("Port", value=defaults.port)
        user = st.text_input("User", value=defaults.user)
        # Never pre-fill the password: a widget's value is sent to the browser.
        password = st.text_input(
            "Password",
            type="password",
            placeholder="Uses DB_PASSWORD if empty" if defaults.password else "",
        )
        database = st.text_input("Database", value=defaults.database)
        if st.button("Connect"):
            password = password or defaults.password
            old = st.session_state.pop("db", None)
            if old is not None:
                old.engine.dispose()
            with st.spinner("Connecting..."):
                try:
                    st.session_state.db = connect(build_url(user, password, host, port, database))
                    st.session_state.db_label = f"{database} on {host}:{port}"
                except (SQLAlchemyError, ValueError) as exc:
                    st.error(f"Could not connect. {describe_error(exc, secret=password)}")
        if "db" in st.session_state:
            st.success(f"Connected to {st.session_state.db_label}.")
            if not st.session_state.db.schema.strip():
                st.warning("No tables are visible to this user.")
        st.caption(f"Model: {config.openai_model()}")


def to_history(messages: list[dict]) -> list[BaseMessage]:
    return [
        HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])
        for m in messages
    ]


def ask(question: str) -> ChatResult:
    history = to_history(st.session_state.messages)
    try:
        return get_response(question, history, st.session_state.db, make_llm())
    except Exception as exc:  # an OpenAI error, for example: show it instead of a stack trace
        error = f"{type(exc).__name__}: {exc}"
        return ChatResult(answer=f"Sorry, the request failed. {error}", error=error)


connection_sidebar()

with st.chat_message("assistant"):
    st.markdown(GREETING)
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(escape_dollars(message["content"]))
        if "result" in message:
            show_details(message["result"])

question = st.chat_input("Ask a question about your data")
if question and question.strip():
    if "db" not in st.session_state:
        st.info("Connect to a database in the sidebar first.")
    else:
        with st.chat_message("user"):
            st.markdown(escape_dollars(question))
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                result = ask(question)
            st.markdown(escape_dollars(result.answer))
            show_details(result)
        st.session_state.messages += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": result.answer, "result": result},
        ]
