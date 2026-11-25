"""Run the supported orders-date optimization workflow and emit a JSON report."""

import argparse
import json
import sys
import os
from pathlib import Path
from dotenv import dotenv_values

from sql_optimization_agent.batch_sql import run_batch
from sql_optimization_agent.qwen_workflow import FIXTURES
from sql_optimization_agent.customer_metrics import FIXTURES as CUSTOMER_FIXTURES, SCENARIO as CUSTOMER_SCENARIO
from sql_optimization_agent.tools.order_items_benchmark import FIXTURES as ITEM_FIXTURES
from sql_optimization_agent.order_items_qwen import MODEL_FIXTURES
from sql_optimization_agent.sql_intake import SQLIntakeError, read_query
from sql_optimization_agent.sql_diff import attach_sql_diff
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, OptimizationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(dict.fromkeys((*FIXTURES,*ITEM_FIXTURES,*MODEL_FIXTURES,*CUSTOMER_FIXTURES))),
                        help="Controlled model response or items candidate; still uses real PostgreSQL")
    parser.add_argument("--scenario", default=None, help="Supported scenario identifier")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Order calendar date; default is defined by the service")
    parser.add_argument("--qwen", action="store_true", help="Explicit model-advice mode for order items or customer metrics")
    parser.add_argument("--repair-on-mismatch", action="store_true",
                        help="Date scenario only: allow one repair request after a database result mismatch")
    parser.add_argument("--sql-file", help="Recognize the supported original orders SELECT from a local UTF-8 file")
    parser.add_argument("--sql-files", nargs='+', help="Explicit SQL paths processed serially in input order")
    parser.add_argument('--config-file', help='Explicit local dotenv configuration; never printed')
    parser.add_argument('--customer-scope', choices=('all','narrow'), default=None, help='Customer metrics only: all customers or customer 1')
    args = parser.parse_args()
    if args.customer_scope is not None and args.scenario != CUSTOMER_SCENARIO:
        parser.error('--customer-scope requires --scenario customer_metrics_2025')
    if args.scenario == CUSTOMER_SCENARIO and not args.config_file:
        parser.error('Customer metrics requires an explicit isolated --config-file')
    if args.config_file and (args.sql_files is not None or args.sql_file is not None):
        parser.error('--config-file is for explicit scenario calls only')
    service_options={"fixture":args.fixture}
    if args.scenario==CUSTOMER_SCENARIO:
        service_options["enabled_scenarios"]=(CUSTOMER_SCENARIO,)
    if args.config_file:
        if not Path(args.config_file).is_file():parser.error('Configuration file does not exist')
        service_options['config']={**dotenv_values(args.config_file,interpolate=False),**os.environ}

    if args.sql_files is not None:
        if args.sql_file is not None or args.scenario is not None or args.date is not None:
            parser.error("--sql-files conflicts with --sql-file, --scenario or --date")
        report = run_batch(args.sql_files,service_factory=OptimizationService,fixture=args.fixture,
                           use_qwen=args.qwen,repair_on_mismatch=args.repair_on_mismatch)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['status']=='completed' else 1
    if args.sql_file is not None and (args.scenario is not None or args.date is not None):
        parser.error("--sql-file conflicts with explicit --scenario or --date")
    scenario = ORDERS_DATE_SCENARIO if args.scenario is None else args.scenario
    intake = None
    if args.sql_file is not None:
        try:
            scenario, args.date = read_query(args.sql_file)
        except SQLIntakeError as error:
            report = attach_sql_diff(attach_recommendation(attach_assessment({
                "status":error.status,"stage":"sql_intake","reason":str(error),
                "model_requests":0,"benchmark":None})))
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 1
        intake = {"source":"sql_file","dialect":"postgres","recognized_scenario":scenario,
                  "extracted_date":args.date,"execution":"repository_parameterized_template"}
    try:
        report = OptimizationService(**service_options).optimize(scenario, date=args.date,use_qwen=args.qwen,
                            **({"customer_scope": args.customer_scope or "all"} if scenario==CUSTOMER_SCENARIO else {}),
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
