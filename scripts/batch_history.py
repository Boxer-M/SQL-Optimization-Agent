"""Offline batch import, list, show and append-only manual review."""
import argparse
import json
import sys
from pathlib import Path
from sql_optimization_agent.batch_history import HistoryError,import_report,history,detail,review


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,default=Path(__file__).resolve().parents[1]/'.batch-history')
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('import');p.add_argument('report')
    commands.add_parser('list')
    p=commands.add_parser('show');p.add_argument('batch_id');p.add_argument('--entry',type=int)
    p=commands.add_parser('review');p.add_argument('batch_id');p.add_argument('entry',type=int)
    p.add_argument('decision',choices=['accept','defer','reject']);p.add_argument('--reviewer',required=True);p.add_argument('--reason',required=True)
    args=parser.parse_args()
    try:
        if args.command=='import':result=import_report(args.store,args.report)
        elif args.command=='list':result=history(args.store)
        elif args.command=='show':result=detail(args.store,args.batch_id,args.entry)
        else:result=review(args.store,args.batch_id,args.entry,args.decision,args.reviewer,args.reason)
        print(json.dumps({'status':'ok','result':result},ensure_ascii=False,indent=2));return 0
    except (HistoryError,OSError):
        error=sys.exception()
        print(json.dumps({'status':'error','reason':str(error) if isinstance(error,HistoryError) else 'Unable to access history storage'},ensure_ascii=False),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
