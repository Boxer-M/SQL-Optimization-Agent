"""Diagnose one local EXPLAIN JSON file, or compare original and candidate files."""

import argparse
import json
import sys

from sql_optimization_agent.tools.plan_diagnosis import PlanInputError,load_and_diagnose
from sql_optimization_agent.tools.plan_comparison import load_and_compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan_file',help='Local PostgreSQL EXPLAIN FORMAT JSON file')
    parser.add_argument('candidate_file',nargs='?',help='Optional candidate EXPLAIN JSON file; first file is original')
    args=parser.parse_args()
    try:
        report = load_and_compare(args.plan_file,args.candidate_file) if args.candidate_file else load_and_diagnose(args.plan_file)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except PlanInputError as error:
        print(json.dumps({'status':'error','error_code':error.code,'message':str(error)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
