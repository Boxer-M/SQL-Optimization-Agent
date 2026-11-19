"""Evaluate the fixed 12-case Qwen matrix; simulation is the safe default."""

import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from sql_optimization_agent.batch_evaluation import evaluate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Allow at most 12 real model requests; no retries')
    parser.add_argument('--output',type=Path,help='New JSON output path; existing files are never overwritten')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=args.output or root/'reports'/('qwen_evaluation_live.json' if args.live else 'qwen_evaluation_simulated.json')
    config={**dotenv_values(root/'.env',interpolate=False),**os.environ}
    try:
        def progress(row):
            print(f"{row['scenario']} {row['date']} #{row['repeat']}: {row['status']}",file=sys.stderr,flush=True)
        report=evaluate(config,output,live=args.live,progress=progress)
        print(json.dumps({'report':str(output),'status':report['status'],'summary':report['summary']},ensure_ascii=False,indent=2))
        return 0 if report['status']=='completed' and all(r['status'] in ('completed','no_suggestion','no_rewrite') for r in report['results']) else 1
    except FileExistsError:
        print('Output already exists; use --output with a new path. No cases executed.',file=sys.stderr)
        return 2
    except OSError:
        print('Unable to read configuration or write the evaluation report.',file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
