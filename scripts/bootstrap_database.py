from __future__ import annotations

import os
import re
from getpass import getpass

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from recalllease.schema import (
    APP_DATABASE,
    APP_USER,
    LEAST_PRIVILEGE_STATEMENTS,
    SCHEMA_STATEMENTS,
    SCHEMA_VALIDATION_STATEMENTS,
)

DATABASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MINIMUM_PASSWORD_LENGTH = 32


def _connection_info(
    admin_database_url: str,
    *,
    database_name: str,
    user: str | None = None,
    password: str | None = None,
) -> str:
    parameters = conninfo_to_dict(admin_database_url)
    parameters["dbname"] = database_name
    if user is not None:
        parameters["user"] = user
    if password is not None:
        parameters["password"] = password
    return make_conninfo(**parameters)


def _read_configuration() -> tuple[str, str, str]:
    admin_database_url = os.environ.get("RECALLLEASE_ADMIN_DATABASE_URL", "").strip()
    if not admin_database_url:
        raise RuntimeError("RECALLLEASE_ADMIN_DATABASE_URL is required")
    try:
        admin_connection_info = conninfo_to_dict(admin_database_url)
    except psycopg.ProgrammingError as error:
        raise RuntimeError(
            "RECALLLEASE_ADMIN_DATABASE_URL is not a valid PostgreSQL DSN"
        ) from error
    if admin_connection_info.get("sslmode") != "verify-full":
        raise RuntimeError("RECALLLEASE_ADMIN_DATABASE_URL must use sslmode=verify-full")

    database_name = os.environ.get("RECALLLEASE_DATABASE_NAME", APP_DATABASE).strip()
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise RuntimeError("RECALLLEASE_DATABASE_NAME must be a lowercase SQL identifier")

    password = os.environ.get("RECALLLEASE_APP_PASSWORD") or getpass(
        f"New password for {APP_USER} (hidden): "
    )
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise RuntimeError(
            f"RECALLLEASE_APP_PASSWORD must be at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    return admin_database_url, database_name, password


def bootstrap() -> None:
    admin_database_url, database_name, password = _read_configuration()

    with psycopg.connect(admin_database_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE IF NOT EXISTS {}").format(sql.Identifier(database_name))
        )
        connection.execute(sql.SQL("CREATE USER IF NOT EXISTS {}").format(sql.Identifier(APP_USER)))
        connection.execute(
            sql.SQL("ALTER USER {} WITH PASSWORD {}").format(
                sql.Identifier(APP_USER),
                sql.Literal(password),
            )
        )

    target_admin_url = _connection_info(
        admin_database_url,
        database_name=database_name,
    )
    with psycopg.connect(target_admin_url, autocommit=True) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name),
                sql.Identifier(APP_USER),
            )
        )
        for statement in LEAST_PRIVILEGE_STATEMENTS:
            connection.execute(statement)

    runtime_database_url = _connection_info(
        admin_database_url,
        database_name=database_name,
        user=APP_USER,
        password=password,
    )
    with psycopg.connect(runtime_database_url, autocommit=True) as connection:
        for statement in SCHEMA_VALIDATION_STATEMENTS:
            connection.execute(statement)

    print(
        "RecallLease database bootstrap verified: dedicated database, runtime login, "
        "schema, and least-privilege reads are ready."
    )


if __name__ == "__main__":
    bootstrap()
