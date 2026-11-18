"""Standalone fixed-data acceptance benchmark; shares the scenario's validation engine."""

import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from sql_optimization_agent.db import DatabaseConfigurationError, DatabaseConnectionError
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import BenchmarkValidationError, json_value
from sql_optimization_agent.tools.order_items_benchmark import run as run_items


def run(config):
    return run_items(config, OrderDateContext.parse('2025-06-15'), require_coverage=True)


def main():
    if len(sys.argv) != 1:
        print('This fixed acceptance script accepts no arguments.',file=sys.stderr)
        return 2
    try:
        config = {**dotenv_values(Path(__file__).resolve().parents[1]/'.env',interpolate=False),**os.environ}
        report = run(config)
        print(json.dumps(report,ensure_ascii=False,indent=2,default=json_value))
        return 0 if report['status']=='completed' else 1
    except (DatabaseConfigurationError,DatabaseConnectionError,BenchmarkValidationError) as error:
        print(str(error),file=sys.stderr)
        return 1
    except OSError:
        print('Unable to read configuration or access the database.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
