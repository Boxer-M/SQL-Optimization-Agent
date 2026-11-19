"""Real localhost HTTP and stdio MCP acceptance; controlled replies, real PostgreSQL."""

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SCENARIO='orders_date_filter_2025_06_15'
ROOT=Path(__file__).resolve().parents[1]


def validate(report,enabled,success):
    assert report['target_date']=='2025-06-15'
    assert len(report['attempts'])==(2 if enabled else 1)
    assert report['model_requests']==0
    for index,attempt in enumerate(report['attempts']):
        comparison=attempt['comparison']
        assert comparison['original_rows']==825
        assert comparison['candidate_rows']==(825 if index==1 and success else 826)
        assert (attempt['benchmark'] is not None)==(index==1 and success)
    if not enabled or not success:
        assert report['recommendation']['decision']=='keep_original'
        assert report['benchmark'] is None
    else:
        assert report['comparison']['equivalent'] is True
        verdict=report['performance_assessment']['verdict']
        assert report['recommendation']['decision']==('use_candidate' if verdict=='improved' else 'keep_original')


def semantic(report):
    return {'status':report['status'],'attempts':[
        {'status':a['status'],'rows':[a['comparison']['original_rows'],a['comparison']['candidate_rows']],
         'equivalent':a['comparison']['equivalent'],'timed':a['benchmark'] is not None} for a in report['attempts']],
        'recommendation':report['recommendation']}


async def mcp_call(env):
    params=StdioServerParameters(command=sys.executable,args=['-m','sql_optimization_agent.mcp_server'],
                                 cwd=str(ROOT),env=env)
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=180)) as session:
            await session.initialize()
            tools=await session.list_tools()
            assert 'optimize_orders_date' in [tool.name for tool in tools.tools]
            result=await session.call_tool('optimize_orders_date',{'scenario':SCENARIO})
            return result.structuredContent


def http_call(env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with tempfile.TemporaryFile() as logs:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','sql_optimization_agent.api:app',
                                  '--host','127.0.0.1','--port',str(port),'--log-level','warning'],
                                 cwd=ROOT,env=env,stdout=logs,stderr=logs)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=180,trust_env=False) as client:
                deadline=time.monotonic()+15
                while True:
                    if process.poll() is not None:raise RuntimeError('HTTP service exited before readiness')
                    try:
                        if client.get('/openapi.json').status_code==200:break
                    except httpx.ConnectError:pass
                    if time.monotonic()>deadline:raise RuntimeError('HTTP readiness timeout')
                    time.sleep(0.05)
                response=client.post('/optimize',json={'scenario':SCENARIO})
                assert response.status_code==200
                return response.json()
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/date_repair_interfaces_acceptance.json')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Choose a new report path')
    report={'source':'Controlled model responses, actual loopback HTTP server and stdio MCP, real local synthetic PostgreSQL data',
            'live_qwen_report_reused':{'commit':'c0bd1e9d0dfd15b9a792f3269d1471e684d59b5d',
                'url':'https://github.com/Boxer-M/SQL-Optimization-Agent/blob/c0bd1e9d0dfd15b9a792f3269d1471e684d59b5d/reports/date_repair_live_evaluation.json',
                'new_live_requests':0},'results':[]}
    for name,enabled,success in [('disabled',False,True),('repair_success',True,True),('repair_failure',True,False)]:
        env={**os.environ,'SQL_OPTIMIZATION_MODEL_FIXTURE':'repair-success' if success else 'repair-failure',
             'QWEN_API_KEY':'','SQL_OPTIMIZATION_ITEMS_MODE':'controlled'}
        if enabled:env['SQL_OPTIMIZATION_DATE_REPAIR']='true'
        else:env.pop('SQL_OPTIMIZATION_DATE_REPAIR',None)
        http=http_call(env);mcp=asyncio.run(mcp_call(env))
        validate(http,enabled,success);validate(mcp,enabled,success)
        assert semantic(http)==semantic(mcp), 'Inspect differing samples/recommendations; do not silently equate them'
        report['results'].append({'path':name,'http':http,'mcp':mcp,'semantics_match':True})
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(name+': HTTP and stdio MCP passed',flush=True)
    print('Report: '+str(args.output))


if __name__=='__main__':main()
