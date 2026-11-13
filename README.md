# Chat with your database (MySQL + LangChain)

A Streamlit chat app for asking a MySQL database questions in plain English. You type
a question, a large language model writes the SQL, the app runs it against your
database, and you get the answer back in plain language. The conversation is kept, so
follow-up questions ("and which of those sold the most?") work the way you'd expect.

## How it works

Each question goes through two LangChain chains:

```
your question ─┐
schema ────────┼─► Chain 1: LLM writes a SQL query ──► MySQL runs it
chat history ──┘                                          │
                                                          ▼
                Chain 2: LLM turns the question, query and result
                into a plain-English answer ──► shown in the chat
```

1. **SQL generation.** The prompt contains the database schema (read with LangChain's
   `SQLDatabase.get_table_info()`), the chat history and your question. The model must
   return only a SQL query.
2. **Execution.** The query runs against your database through `SQLDatabase.run()`.
3. **Answer.** A second prompt receives the schema, the query and its result, and
   writes the reply you see.

The prompts include two worked examples written for the
[Chinook sample database](https://github.com/lerocha/chinook-database) (artists,
albums, tracks, invoices), which is the default database in the connection settings.

## Tech stack

- Python, Streamlit (chat UI and connection sidebar)
- LangChain (`langchain`, `langchain-openai`, `langchain-community`'s `SQLDatabase`)
- OpenAI `gpt-3.5-turbo` for both the SQL and the answer
- MySQL through `mysql-connector-python` (SQLAlchemy URI `mysql+mysqlconnector://`)
- `python-dotenv` for configuration

## Getting started

### 1. Prerequisites

- Python 3.10 or later
- A running MySQL server. To try it quickly, load the Chinook sample database.
- An OpenAI API key

### 2. Install

```bash
git clone https://github.com/aryanonline/SQL-chatbot.git
cd SQL-chatbot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install langchain-community   # needed for SQLDatabase on newer LangChain versions
```

### 3. Configure

Create a `.env` file in the project folder:

```bash
OPENAI_API_KEY=sk-...
```

### 4. Create a read-only MySQL user (recommended)

The app runs whatever SQL the model writes. Connect with a user that can only read:

```sql
CREATE USER 'chatbot_reader'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
GRANT SELECT ON chinook.* TO 'chatbot_reader'@'localhost';
```

### 5. Run

```bash
streamlit run app.py
```

Then, in the browser:

1. Fill in host, port, user, password and database name in the sidebar and click **Connect**.
2. Ask questions in the chat box.

## Example questions (Chinook)

- Which 3 artists have the most tracks?
- How many customers are there in each country?
- What were total sales by year?
- Which genre has the longest average track length?

## Project structure

```
app.py             Streamlit UI, the two LangChain chains, and the MySQL connection
requirements.txt   Python dependencies
```

## Limitations

- **No query guardrails.** The generated SQL is executed as written. Use a read-only
  user (step 4) and never point this at a production database with write access.
- **Connect before chatting.** Sending a message before clicking *Connect* raises an error.
- **Whole schema in every prompt.** Very large databases can go past the model's context
  window. Selecting only the relevant tables would fix this.
- **No validation or retry.** If the model writes invalid SQL, the error is not caught
  and the query is not regenerated.
- **Prefilled credentials.** The sidebar starts with example values (`practice` / `admin`)
  for local testing. Replace them with your own.
- **Unpinned dependencies.** `requirements.txt` has no versions and doesn't list
  `langchain-community`, so new LangChain releases can break the install. `langchain-groq`
  is listed but isn't used yet.

## Ideas for next steps

- Read-only enforcement in code (allow `SELECT` only) as well as at the database
- Validate the SQL and retry automatically when execution fails
- Show the generated SQL next to each answer for transparency
- Support PostgreSQL and SQLite through the same `SQLDatabase` interface
- Use a newer model, or a Groq-hosted model through `langchain-groq`
