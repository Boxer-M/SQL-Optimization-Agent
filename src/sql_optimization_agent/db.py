"""Read-only PostgreSQL connections using application credentials only."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import os

import psycopg


class DatabaseConfigurationError(ValueError):
    """Missing or invalid configuration; messages contain variable names only."""


class DatabaseConnectionError(RuntimeError):
    """Sanitized database failure, without credentials or connection strings."""


@contextmanager
def readonly_connection(
    config: Mapping[str, str | None] | None = None,
) -> Iterator[psycopg.Connection]:
    """Use with this context manager to close the connection on every exit.

    The caller supplies configuration or sets environment variables. This module
    does not discover .env files or use the initialization account's password.
    """
    values = os.environ if config is None else config
    required = ("POSTGRES_READER_USER", "POSTGRES_READER_PASSWORD")
    missing = [key for key in required if not values.get(key, "") or not values[key].strip()]
    if missing:
        raise DatabaseConfigurationError("Missing required configuration: " + ", ".join(missing))
    if values["POSTGRES_READER_USER"] != "sql_optimization_reader":
        raise DatabaseConfigurationError("Invalid configuration: POSTGRES_READER_USER")
    try:
        port = int(values.get("POSTGRES_PORT", "5434"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise DatabaseConfigurationError("Invalid configuration: POSTGRES_PORT") from None

    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=port,
            dbname="sql_optimization",
            user=values["POSTGRES_READER_USER"],
            password=values["POSTGRES_READER_PASSWORD"],
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        ) as connection:
            connection.read_only = True
            yield connection
    except psycopg.Error:
        raise DatabaseConnectionError(
            "PostgreSQL operation failed; check service, application credentials and permissions."
        ) from None
