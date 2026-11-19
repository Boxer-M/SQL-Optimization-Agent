"""Read only a specified EXPLAIN JSON file and print a structured diagnosis."""

import argparse
import json
import sys

from sql_optimization_agent.tools.plan_diagnosis import PlanInputError,load_and_diagnose


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan_file',help='Local PostgreSQL EXPLAIN FORMAT JSON file')
    args=parser.parse_args()
    try:
        print(json.dumps(load_and_diagnose(args.plan_file),ensure_ascii=False,indent=2))
        return 0
    except PlanInputError as error:
        print(json.dumps({'status':'error','error_code':error.code,'message':str(error)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
