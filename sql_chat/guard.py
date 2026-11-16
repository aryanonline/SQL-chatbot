"""A conservative check that model-written SQL is a single read-only SELECT.

This is the first safety layer. db.connect turns off multi-statement batches on MySQL
connections, and db.run_read_only runs the query inside a READ ONLY transaction. None of
them replaces connecting with a database user that can only read.

The keyword checks use sqlparse's tokens, but sqlparse does not read quotes and comments
exactly the way MySQL does, and text it takes for a string or a comment is never checked.
So the guard also finds the string literals, quoted names and comments again with MySQL's
rules (_mysql_spans) and refuses the query when the two readings differ.
"""

from __future__ import annotations

import re

import sqlparse
from sqlparse import tokens as T
from sqlparse.exceptions import SQLParseError
from sqlparse.lexer import tokenize
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
# Control characters other than tab, line feed and carriage return. MySQL ends a line
# comment at a NUL byte, which _mysql_spans does not model, so none of them is allowed.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DIFFERENT_READING = (
    "MySQL would read the quotes or comments in this query differently from this check."
)


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
    if "\\" in sql:
        # MySQL reads a backslash in a string as an escape and sqlparse does not always,
        # so the two can disagree about where a string ends.
        return False, "Backslashes are not allowed."
    if _CONTROL.search(sql):
        return False, "Control characters are not allowed."
    if "delimiter" in sql.lower():
        # mysql-connector rewrites a query that contains DELIMITER before sending it.
        return False, "The word DELIMITER is not allowed."
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
    if _sqlparse_spans(sql) != _mysql_spans(sql):
        return False, DIFFERENT_READING
    return True, ""


def _strip_end(sql: str) -> str:
    return _TRAILING.sub("", sql).strip()


def _starts_dash_comment(text: str, i: int) -> bool:
    """MySQL starts a comment at "--" only when an ASCII space or control character (or
    the end of the query) follows it. Other Unicode spaces do not count."""
    if not text.startswith("--", i):
        return False
    after = text[i + 2 : i + 3]
    return after == "" or after <= " " or after == "\x7f"


def _mysql_spans(sql: str) -> list[tuple[int, int]] | None:
    """Return (start, end) for each string literal, quoted name and comment, found the
    way MySQL's lexer finds them.

    Assumes there are no backslashes (is_read_only refuses them), so a quote character
    is escaped only by doubling it. Returns None for an unterminated quote or comment,
    which MySQL rejects as a syntax error.
    """
    spans = []
    i, n = 0, len(sql)
    while i < n:
        char = sql[i]
        if char in "'\"`":
            close = sql.find(char, i + 1)
            while close != -1 and sql.startswith(char, close + 1):
                close = sql.find(char, close + 2)  # a doubled quote stands for one quote
            if close == -1:
                return None
            end = close + 1
        elif char == "#" or _starts_dash_comment(sql, i):
            newline = sql.find("\n", i)  # only a line feed ends the comment
            end = n if newline == -1 else newline
        elif sql.startswith("/*", i):
            close = sql.find("*/", i + 2)
            if close == -1:
                return None
            end = close + 2
        else:
            i += 1
            continue
        spans.append((i, end))
        i = end
    return spans


def _sqlparse_spans(sql: str) -> list[tuple[int, int]]:
    """The same spans as _mysql_spans, as sqlparse's lexer sees them."""
    spans = []
    start = 0
    for ttype, value in tokenize(sql):
        end = start + len(value)
        if ttype in T.Comment:
            # sqlparse keeps the line feed that ends a line comment; MySQL does not.
            spans.append((start, end - 1 if value.endswith("\n") else end))
        elif ttype in T.String or ttype is T.Literal or (ttype in T.Name and value[0] in "`[´"):
            # T.Literal alone is a $$-quoted string; [name] and ´name´ are quoted names.
            spans.append((start, end))
        start = end
    return spans


def _check_token(token: Token, next_token: Token | None) -> str:
    """Return why this token makes the query unsafe, or "" if it is fine."""
    ttype = token.ttype
    if ttype in T.Comment.Multiline.Hint:
        # Hints such as /*+ MAX_EXECUTION_TIME(...) */ could lift the time limit.
        return "Optimizer hints (/*+ ... */) are not allowed."
    if ttype in T.Comment:
        # sqlparse takes "--" followed by anything as a comment; MySQL does not.
        if token.value.startswith("--") and not (
            len(token.value) > 2 and _starts_dash_comment(token.value, 0)
        ):
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
