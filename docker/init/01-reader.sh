#!/bin/bash
# Creates chatbot_reader, the user the chat app connects as: it can only SELECT
# from the Chinook database. Its password comes from READER_PASSWORD.
#
# The mysql image runs the files in /docker-entrypoint-initdb.d in name order,
# once, when the container starts with an empty data volume, so this runs after
# 00-chinook.sql. The image may source or execute this file; both work.
set -eo pipefail

# Escape backslashes and single quotes so the password is a valid SQL string literal.
reader_password=${READER_PASSWORD//\\/\\\\}
reader_password=${reader_password//\'/\\\'}

mysql --defaults-extra-file=<(printf '[client]\npassword="%s"\n' "${MYSQL_ROOT_PASSWORD}") \
	--protocol=socket --socket="${SOCKET:-/var/run/mysqld/mysqld.sock}" --user=root <<EOSQL
CREATE USER IF NOT EXISTS 'chatbot_reader'@'%' IDENTIFIED BY '${reader_password}';
GRANT SELECT ON Chinook.* TO 'chatbot_reader'@'%';
EOSQL
