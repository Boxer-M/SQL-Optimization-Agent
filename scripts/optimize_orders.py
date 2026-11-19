"""Run the supported orders-date optimization workflow and emit a JSON report."""

import argparse
import json
import sys

from sql_optimization_agent.qwen_workflow import FIXTURES
from sql_optimization_agent.tools.order_items_benchmark import FIXTURES as ITEM_FIXTURES
from sql_optimization_agent.order_items_qwen import MODEL_FIXTURES
from sql_optimization_agent.sql_intake import SQLIntakeError, read_date_query
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, OptimizationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(dict.fromkeys((*FIXTURES,*ITEM_FIXTURES,*MODEL_FIXTURES))),
                        help="Controlled model response or items candidate; still uses real PostgreSQL")
    parser.add_argument("--scenario", default=None, help="Supported scenario identifier")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Order calendar date; default is defined by the service")
    parser.add_argument("--qwen", action="store_true", help="Explicit model-advice mode for orders_with_items_count")
    parser.add_argument("--repair-on-mismatch", action="store_true",
                        help="Date scenario only: allow one repair request after a database result mismatch")
    parser.add_argument("--sql-file", help="Recognize the supported original date-filter SELECT from a local UTF-8 file")
    args = parser.parse_args()
    if args.sql_file is not None and (args.scenario is not None or args.date is not None):
        parser.error("--sql-file conflicts with explicit --scenario or --date")
    scenario = ORDERS_DATE_SCENARIO if args.scenario is None else args.scenario
    intake = None
    if args.sql_file is not None:
        try:
            args.date = read_date_query(args.sql_file)
        except SQLIntakeError as error:
            report = attach_recommendation(attach_assessment({
                "status":error.status,"stage":"sql_intake","reason":str(error),
                "model_requests":0,"benchmark":None}))
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 1
        intake = {"source":"sql_file","dialect":"postgres","recognized_scenario":scenario,
                  "extracted_date":args.date,"execution":"repository_parameterized_template"}
    try:
        report = OptimizationService(fixture=args.fixture).optimize(scenario, date=args.date,use_qwen=args.qwen,
                            **({"repair_on_mismatch": True} if args.repair_on_mismatch else {}))
        if intake is not None:
            report["sql_intake"] = intake
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in ("completed", "no_suggestion", "no_rewrite") else 1
    except OSError:
        print("Unable to read local configuration or access services.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
