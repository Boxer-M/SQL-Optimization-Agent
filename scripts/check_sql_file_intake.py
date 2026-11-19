"""Controlled CLI intake acceptance with real PostgreSQL and blocked model calls."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

import httpx
import psycopg

from sql_optimization_agent.batch_evaluation import benchmark_summary
from sql_optimization_agent.tools.sql_benchmark import ORIGINAL

ROOT=Path(__file__).resolve().parents[1]
SCENARIO='orders_date_filter_2025_06_15'


def invoke(arguments):
    stdout=io.StringIO();stderr=io.StringIO()
    with patch.object(sys,'argv',['optimize_orders.py',*arguments]),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
        try:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
        except SystemExit as stopped:code=stopped.code
    return code,json.loads(stdout.getvalue()) if stdout.getvalue() else {'argument_error':stderr.getvalue().splitlines()[-1]}


def compact(report):
    if report.get('benchmark'):report['benchmark']=benchmark_summary(report['benchmark'])
    for attempt in report.get('attempts',[]):
        if attempt.get('benchmark'):attempt['benchmark']=benchmark_summary(attempt['benchmark'])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/sql_file_intake_acceptance.json')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Choose a new report path')
    report={'source':'Actual CLI, controlled model responses, real local synthetic PostgreSQL data',
            'accepted':[],'rejected':[]}
    with patch('sql_optimization_agent.qwen_workflow.request_suggestion',side_effect=AssertionError('Real model forbidden')) as model,patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('HTTP forbidden')) as http:
        for day in ('2025-06-15','2025-06-16'):
            path=ROOT/'sql/examples'/f"orders_date_{day.replace('-','_')}.sql"
            file_code,file_report=invoke(['--sql-file',str(path),'--fixture','normal'])
            flag_code,flag_report=invoke(['--scenario',SCENARIO,'--date',day,'--fixture','normal'])
            assert file_code==flag_code==0
            assert file_report['sql_intake']['extracted_date']==day
            assert file_report['comparison']['equivalent'] is True
            assert file_report['comparison']['original_rows']==(825 if day=='2025-06-15' else 823)
            for key in ('target_date','date_context','comparison','candidate','suggestion'):
                assert file_report[key]==flag_report[key],key
            assert file_report['diagnostic_context']['original_sql']==flag_report['diagnostic_context']['original_sql']
            assert file_report['diagnostic_context']['original_parameters']==[day]
            report['accepted'].append({'date':day,'matching_validation_fields':True,
                                      'sql_file':compact(file_report),'scenario_date':compact(flag_report)})
        assert model.call_count==http.call_count==0
        rejected=[('multiple',ORIGINAL+'; SELECT 1','unsupported_query_shape'),
                  ('non_select','DELETE FROM public.orders','unsupported_query_shape'),
                  ('extra_filter',ORIGINAL+" AND status = 'paid'",'unsupported_query_shape'),
                  ('projection',ORIGINAL.replace('customer_id,','order_id,'),'unsupported_query_shape'),
                  ('invalid_date',ORIGINAL.replace('2025-06-15','2025-02-29'),'invalid_date'),
                  ('syntax','SELECT (','invalid_sql')]
        with tempfile.TemporaryDirectory() as temporary,patch.object(psycopg,'connect',side_effect=AssertionError('Rejected input connected')) as db,patch.object(psycopg.Connection,'connect',side_effect=AssertionError('Rejected input connected')) as db_class:
            for name,sql,status in rejected:
                path=Path(temporary)/(name+'.sql');path.write_text(sql)
                code,outcome=invoke(['--sql-file',str(path),'--fixture','normal'])
                assert code==1 and outcome['status']==status
                report['rejected'].append({'case':name,'exit_code':code,'report':outcome})
            for option,value in [('--date','2025-06-15'),('--scenario',SCENARIO)]:
                code,outcome=invoke(['--sql-file','unread.sql',option,value])
                assert code==2 and 'conflicts' in outcome['argument_error']
                report['rejected'].append({'case':'conflict_'+option,'exit_code':code,'report':outcome})
            report['rejection_calls']={'database_connections':db.call_count+db_class.call_count,
                                       'model_calls':model.call_count,'http_requests':http.call_count}
            assert all(count==0 for count in report['rejection_calls'].values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(args.output),'accepted_dates':2,'rejected_cases':len(report['rejected']),
                      'rejection_calls':report['rejection_calls']},indent=2))


if __name__=='__main__':main()
