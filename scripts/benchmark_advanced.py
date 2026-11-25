"""Fixed advanced SQL experiments, only in an explicitly isolated database."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from dotenv import dotenv_values
from sql_optimization_agent.benchmarks.advanced import run_suite,admin_connection,guard
from sql_optimization_agent.tools.sql_benchmark import json_value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',required=True)
    parser.add_argument('--initialize',action='store_true',help='Mark an empty isolated database; refuses nonempty unmarked databases')
    parser.add_argument('--sizes',nargs='+',type=int,default=[300000,1000000])
    parser.add_argument('--seed',type=int,default=20261125)
    parser.add_argument('--report',default='reports/advanced_sql_benchmarks.json')
    args=parser.parse_args()
    config=dotenv_values(args.env_file)
    required=('POSTGRES_HOST','POSTGRES_PORT','POSTGRES_READER_USER','POSTGRES_READER_PASSWORD','ADVANCED_BENCHMARK_ADMIN_PASSWORD')
    missing=[k for k in required if not config.get(k)]
    if missing:parser.error('Missing configuration: '+', '.join(missing))
    if not args.sizes or any(not 1<=n<=1000000 for n in args.sizes):parser.error('sizes must be 1..1000000')
    if args.sizes!=sorted(set(args.sizes)):parser.error('sizes must be unique and ascending')
    if not 0<=args.seed<=2147483647:parser.error('seed must be 0..2147483647')
    report_started=False
    sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    def save(report):
        nonlocal report_started
        report_started=True
        report['baseline_sha']=sha
        path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=json_value)+'\n')
        temporary.replace(path)
    try:
        with admin_connection(config) as c:guard(c,args.initialize)
        run_suite(config,save,sizes=tuple(args.sizes),seed=args.seed,initialize=args.initialize)
    except Exception as error:
        # Never expose a driver error, SQL parameters, passwords or connection strings.
        path=Path(args.report)
        if report_started and path.exists():
            prior=json.loads(path.read_text());prior.update(status='failed',error={'type':type(error).__name__,'message':'Stopped; no credentials or driver error text retained'})
            save(prior)
        safe=str(error) if type(error) is ValueError and str(error).startswith(('Refusing reset:','Invalid deterministic seed','Invalid fixed seed','Reader role has unexpected')) else type(error).__name__
        print('Advanced benchmark stopped: '+safe+'; inspect the sanitized report and local service/configuration.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
