"""A conservative check that model-written SQL is a single read-only SELECT.

This is the first of two safety layers. The second is db.run_read_only, which runs the
query inside a READ ONLY transaction on MySQL. Neither replaces connecting with a
database user that can only read.
"""

from __future__ import annotations

import re

import sqlparse
from sqlparse import tokens as T
from sqlparse.exceptions import SQLParseError
from sqlparse.sql import Token

# Words that reject a query when they appear outside string literals, comments and
# quoted identifiers. Function calls such as REPLACE(Name, 'a', 'b') are still allowed.
FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "DROP", "CREATE", "ALTER",
        "TRUNCATE", "RENAME", "GRANT", "REVOKE", "CALL", "DO", "HANDLER", "LOAD", "LOCK",
        "UNLOCK", "SET", "INTO", "OUTFILE", "DUMPFILE",
    }
)  # fmt: skip
# Functions that can stall the server or read files on it.
FORBIDDEN_FUNCTIONS = frozenset({"SLEEP", "BENCHMARK", "LOAD_FILE"})

_FENCED_BLOCK = re.compile(r"```[ \t]*[\w-]*[ \t]*\n(.*?)```", re.DOTALL)
_INLINE_FENCE = re.compile(r"```(.*?)```", re.DOTALL)
_LABEL = re.compile(r"^(?:sql\s+query|sql)\s*:\s*", re.IGNORECASE)
_TRAILING = re.compile(r"[\s;]+\Z")


def clean_sql(text: str) -> str:
    """Return the SQL from a model reply, without markdown code fences, wrapping
    backticks, a leading "SQL Query:" label or trailing semicolons."""
    match = _FENCED_BLOCK.search(text) or _INLINE_FENCE.search(text)
    sql = (match.group(1) if match else text).strip()
    if len(sql) > 1 and sql[0] == sql[-1] == "`":
        sql = sql[1:-1].strip()
    return _strip_end(_LABEL.sub("", sql))


def is_read_only(sql: str) -> tuple[bool, str]:
    """Check that sql is exactly one SELECT statement (WITH ... SELECT is fine).

    Returns (True, "") or (False, reason). The check errs on the side of rejecting:
    some unusual but harmless queries are refused rather than risk running a bad one.
    """
    sql = _strip_end(sql)
    if not sql:
        return False, "The query is empty."
    if "/*!" in sql:
        return False, "MySQL executable comments (/*! ... */) are not allowed."
    try:
        statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    except SQLParseError:
        return False, "The query could not be parsed."
    if len(statements) != 1:
        return False, "Only one SQL statement is allowed."
    statement = statements[0]
    if statement.get_type() != "SELECT":
        return False, "Only SELECT queries are allowed."

    tokens = [t for t in statement.flatten() if not t.is_whitespace]
    for i, token in enumerate(tokens):
        next_token = tokens[i + 1] if i + 1 < len(tokens) else None
        reason = _check_token(token, next_token)
        if reason:
            return False, reason
    return True, ""


def _strip_end(sql: str) -> str:
    return _TRAILING.sub("", sql).strip()


def _check_token(token: Token, next_token: Token | None) -> str:
    """Return why this token makes the query unsafe, or "" if it is fine."""
    ttype = token.ttype
    if ttype in T.Comment.Multiline.Hint:
        # Hints such as /*+ MAX_EXECUTION_TIME(...) */ could lift the time limit.
        return "Optimizer hints (/*+ ... */) are not allowed."
    if ttype in T.Comment:
        # MySQL treats "--" as a comment only when whitespace follows; sqlparse always does.
        if token.value.startswith("--") and not token.value[2:3].isspace():
            return "'--' must be followed by a space to start a comment."
        return ""
    if ttype in T.Error:
        return "The query could not be parsed."
    if ttype in T.Punctuation and token.value == ";":
        # Trailing semicolons were stripped, so this one separates statements.
        return "Only one SQL statement is allowed."
    if ttype in T.String:
        return ""

    word = token.value.upper()
    if ttype in T.Name:
        name = word.strip("`")
        if name in FORBIDDEN_FUNCTIONS:
            return f"The function {name}() is not allowed."
        quoted = word.startswith("`")
        is_call = next_token is not None and next_token.value == "("
        if not quoted and not is_call and name in FORBIDDEN_KEYWORDS:
            return f"{name} is not allowed in a read-only query."
    elif ttype in T.Keyword:
        for part in word.split():
            if part in FORBIDDEN_KEYWORDS:
                return f"{part} is not allowed in a read-only query."
    return ""
