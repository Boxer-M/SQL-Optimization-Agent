"""Run only the built-in PostgreSQL connection acceptance query."""

import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from sql_optimization_agent.db import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    readonly_connection,
)


CHECK_SQL = """
SELECT current_database(), current_user,
       current_setting('transaction_read_only'),
       (SELECT COUNT(*) FROM public.orders)
"""


def main() -> int:
    if len(sys.argv) != 1:
        print("This script accepts no arguments and runs only its built-in query.", file=sys.stderr)
        return 2
    try:
        root = Path(__file__).resolve().parents[1]
        # An explicitly empty environment value must override the .env value.
        config = {**dotenv_values(root / ".env", interpolate=False), **os.environ}
        with readonly_connection(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CHECK_SQL)
                result = cursor.fetchone()
        if result != ("sql_optimization", "sql_optimization_reader", "on", 300005):
            print("Connection check failed: unexpected database, user, read-only state or row count.", file=sys.stderr)
            return 1
        print(f"database={result[0]}")
        print(f"user={result[1]}")
        print(f"transaction_read_only={result[2]}")
        print(f"orders_count={result[3]}")
        return 0
    except (DatabaseConfigurationError, DatabaseConnectionError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Unable to read local configuration or access the database.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
