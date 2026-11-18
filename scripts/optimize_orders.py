"""Run the supported orders-date optimization workflow and emit a JSON report."""

import argparse
import json
import sys

from sql_optimization_agent.qwen_workflow import FIXTURES
from sql_optimization_agent.tools.order_items_benchmark import FIXTURES as ITEM_FIXTURES
from sql_optimization_agent.order_items_qwen import MODEL_FIXTURES
from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, OptimizationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(dict.fromkeys((*FIXTURES,*ITEM_FIXTURES,*MODEL_FIXTURES))),
                        help="Controlled model response or items candidate; still uses real PostgreSQL")
    parser.add_argument("--scenario", default=ORDERS_DATE_SCENARIO, help="Supported scenario identifier")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Order calendar date; default is defined by the service")
    parser.add_argument("--qwen", action="store_true", help="Explicit model-advice mode for orders_with_items_count")
    args = parser.parse_args()
    try:
        report = OptimizationService(fixture=args.fixture).optimize(args.scenario, date=args.date,use_qwen=args.qwen)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in ("completed", "no_suggestion", "no_rewrite") else 1
    except OSError:
        print("Unable to read local configuration or access services.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
