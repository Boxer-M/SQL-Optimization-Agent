"""Fixed incorrect INNER JOIN control; never imports or calls a model transport."""
import argparse,json
from pathlib import Path
from dotenv import dotenv_values
from sql_optimization_agent.customer_metrics import execute_candidate,finish
from sql_optimization_agent.tools.sql_benchmark import json_value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config-file',required=True)
    p.add_argument('--report',default='reports/customer_metrics_wrong_inner.json');args=p.parse_args()
    values=dotenv_values(args.config_file,interpolate=False)
    config={k:v for k,v in values.items() if k.startswith('POSTGRES_')}
    report=finish(execute_candidate(config,wrong_inner_control=True))
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=json_value)+'\n')
    if report['status']!='result_mismatch' or report['benchmark'] is not None:
        raise RuntimeError('Wrong INNER JOIN control was not rejected before sampling')
    print('Negative control rejected; zero model calls and zero timing samples')


if __name__=='__main__':
    try:main()
    except Exception as error:
        import sys
        print('Negative control failed: '+type(error).__name__+'; no driver details emitted',file=sys.stderr)
        raise SystemExit(1) from None
