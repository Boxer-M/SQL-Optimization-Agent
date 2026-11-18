"""Print the fixed orders benchmark as JSON; accepts no SQL or CLI arguments."""

import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from sql_optimization_agent.db import DatabaseConfigurationError, DatabaseConnectionError
from sql_optimization_agent.tools.sql_benchmark import (
    BenchmarkValidationError, json_value, run_benchmark,
)


def main() -> int:
    if len(sys.argv) != 1:
        print("No arguments accepted; only built-in benchmark queries are allowed.", file=sys.stderr)
        return 2
    try:
        root = Path(__file__).resolve().parents[1]
        config = {**dotenv_values(root / ".env", interpolate=False), **os.environ}
        report = run_benchmark(config)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_value))
        return 0 if report["positive"]["equivalent"] else 1
    except (DatabaseConfigurationError, DatabaseConnectionError, BenchmarkValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("Unable to read local configuration or access the database.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
