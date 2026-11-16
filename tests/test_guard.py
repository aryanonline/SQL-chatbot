import pytest

from sql_chat.guard import clean_sql, is_read_only

ALLOWED = [
    "SELECT Name FROM Artist LIMIT 10",
    "select name from artist",
    "SELECT * FROM Album;",
    "SELECT * FROM Album ;  \n",
    "WITH counts AS (SELECT AlbumId, COUNT(*) AS n FROM Track GROUP BY AlbumId) "
    "SELECT * FROM counts ORDER BY n DESC",
    "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 3) SELECT i FROM n",
    "SELECT Name FROM Track WHERE Name = 'delete'",
    "SELECT Name FROM Track WHERE Name LIKE '%drop table%'",
    "SELECT 'it''s', 'it\\'s' FROM Artist",
    "SELECT REPLACE(Name, 'a', 'b') FROM Artist",
    "SELECT `Name` FROM `Artist`",
    "SELECT `update`, `set` FROM `Order`",
    "-- the top artists\nSELECT Name FROM Artist",
    "SELECT Name FROM Artist /* a comment; with a semicolon */ LIMIT 3",
    "SELECT Name FROM Artist WHERE ArtistId IN (SELECT ArtistId FROM Album)",
    "SELECT 1 UNION SELECT 2",
]

REJECTED = [
    ("", "empty"),
    ("  ;  ", "empty"),
    ("DELETE FROM Artist", "Only SELECT"),
    ("delete from artist where 1 = 1", "Only SELECT"),
    ("UPDATE Artist SET Name = 'x'", "Only SELECT"),
    ("DROP TABLE Artist", "Only SELECT"),
    ("SHOW TABLES", "Only SELECT"),
    ("SELECT 1; DROP TABLE Artist", "one SQL statement"),
    ("SELECT 1; SELECT 2", "one SQL statement"),
    ("select 1; -- trailing comment", "one SQL statement"),
    ("WITH x AS (SELECT 1) DELETE FROM Artist", "Only SELECT"),
    ("SELECT * FROM Artist INTO OUTFILE '/tmp/artists.csv'", "INTO"),
    ("select * from artist into dumpfile '/tmp/a'", "INTO"),
    ("SELECT Name INTO @name FROM Artist LIMIT 1", "INTO"),
    ("SELECT * FROM Artist /*! INTO OUTFILE '/tmp/a' */", "/*!"),
    ("SELECT /*!50000 SLEEP(1) */ 1", "/*!"),
    ("SELECT SLEEP(10)", "SLEEP"),
    ("select sleep (10)", "SLEEP"),
    ("SELECT `SLEEP`(5)", "SLEEP"),
    ("select benchmark(1000000, md5('x'))", "BENCHMARK"),
    ("SELECT LOAD_FILE('/etc/passwd')", "LOAD_FILE"),
    ("SELECT * FROM Artist FOR UPDATE", "UPDATE"),
    ("SELECT * FROM Artist LOCK IN SHARE MODE", "LOCK"),
    ("SELECT outfile FROM t", "OUTFILE"),
    ("SELECT /*+ MAX_EXECUTION_TIME(100000) */ * FROM Track", "hints"),
    ("SELECT 1 --1, SLEEP(5)", "'--'"),
    ("SELECT 'unterminated FROM Artist", "parsed"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_read_only_select(sql):
    assert is_read_only(sql) == (True, "")


@pytest.mark.parametrize(("sql", "reason_part"), REJECTED)
def test_rejects(sql, reason_part):
    ok, reason = is_read_only(sql)
    assert not ok
    assert reason_part in reason


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("SELECT 1;", "SELECT 1"),
        ("  SELECT 1 ;\n", "SELECT 1"),
        ("```sql\nSELECT Name FROM Artist;\n```", "SELECT Name FROM Artist"),
        ("```SQL\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("Here you go:\n```sql\nSELECT 1\n```\nThis returns one row.", "SELECT 1"),
        ("```SELECT 1```", "SELECT 1"),
        ("`SELECT Name FROM Artist`", "SELECT Name FROM Artist"),
        ("SQL Query: SELECT 1", "SELECT 1"),
        ("SELECT `Name` FROM `Artist`", "SELECT `Name` FROM `Artist`"),
    ],
)
def test_clean_sql(reply, expected):
    assert clean_sql(reply) == expected


def test_fenced_sql_is_checked_after_cleaning():
    assert is_read_only(clean_sql("```sql\nSELECT Name FROM Artist;\n```")) == (True, "")
    ok, _ = is_read_only(clean_sql("```sql\nDELETE FROM Artist;\n```"))
    assert not ok
