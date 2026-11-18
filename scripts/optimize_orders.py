"""Run the supported orders-date optimization workflow and emit a JSON report."""

import argparse
import json
import sys

from sql_optimization_agent.qwen_workflow import FIXTURES
from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, OptimizationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(FIXTURES), help="Controlled model response; still uses real PostgreSQL")
    parser.add_argument("--scenario", default=ORDERS_DATE_SCENARIO, help="Supported scenario identifier")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Order calendar date; default is defined by the service")
    args = parser.parse_args()
    try:
        report = OptimizationService(fixture=args.fixture).optimize(args.scenario, date=args.date)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in ("completed", "no_suggestion") else 1
    except OSError:
        print("Unable to read local configuration or access services.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
