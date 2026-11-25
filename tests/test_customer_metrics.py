from collections import Counter
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock,Mock

import httpx
import psycopg
import pytest
from dotenv import dotenv_values
from sql_optimization_agent import customer_metrics as workflow
from sql_optimization_agent.qwen_client import QwenFailure,request_suggestion
from sql_optimization_agent.service import OptimizationService
from sql_optimization_agent.tools.sql_benchmark import QueryResult,measure_pair,_fetch,_compare

_REAL_CONNECT=psycopg.connect


@pytest.mark.parametrize('content',['{}','[]','invalid','{"action":"aggregate_once","action":"no_rewrite","reason":"x"}',
 '{"action":"aggregate_once","reason":"x","sql":"DROP TABLE orders"}',
 '{"action":"inner_join","reason":"x"}','{"action":"aggregate_once","reason":null}'])
def test_action_contract(content):
    with pytest.raises(QwenFailure):workflow.parse_advice(content,allowed_actions=('aggregate_once','no_rewrite'))


@pytest.mark.parametrize('fixture,status',[('customer-aggregate','completed'),('customer-no-rewrite','no_rewrite'),('customer-invalid','invalid_suggestion')])
def test_simulated_workflow_paths(monkeypatch,fixture,status):
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *a:{'original_sql':workflow.ORIGINAL})
    engine=Mock(return_value={'status':'completed','stage':'complete','comparison':{'equivalent':True},
      'candidate':{'sql':workflow.CANDIDATE},'benchmark':{'execution_ms':{'original':[2]*30,'rewritten':[1]*30},'samples_per_query':30}})
    monkeypatch.setattr(workflow,'execute_candidate',engine)
    report=workflow.run({},fixture=fixture)
    assert report['status']==status and report['model_requests']==0
    if status!='completed':engine.assert_not_called();assert report['benchmark'] is None
    else:assert report['recommendation']['decision']=='use_candidate' and report['sql_diff']


@pytest.mark.parametrize('action',['aggregate_once','no_rewrite'])
def test_real_transport_contract_one_post(monkeypatch,action):
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *a:{'original_sql':workflow.ORIGINAL,'date_context':{'start':'2025-01-01','end':'2026-01-01'}})
    engine=Mock(return_value={'status':'completed','comparison':{'equivalent':True},'candidate':{'sql':workflow.CANDIDATE},'benchmark':{'execution_ms':{'original':[2]*30,'rewritten':[1]*30},'samples_per_query':30}})
    monkeypatch.setattr(workflow,'execute_candidate',engine)
    posts=[]
    def handle(req):
        posts.append(req);payload=json.loads(req.content)
        assert 'aggregate_once' in payload['messages'][0]['content']
        assert 'unit-test-secret' not in json.dumps(payload)
        return httpx.Response(200,json={'model':'actual-test-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'action':action,'reason':'test'})}}]})
    monkeypatch.setattr(workflow,'request_suggestion',lambda context,config,**kw:request_suggestion(context,config,transport=httpx.MockTransport(handle),**kw))
    r=workflow.run({'QWEN_API_KEY':'unit-test-secret','QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1','QWEN_MODEL':'requested-model'},use_qwen=True)
    assert len(posts)==1 and r['model_requests']==1 and r['model_id']=='actual-test-model'
    if action=='no_rewrite':engine.assert_not_called();assert r['recommendation']['decision']=='keep_original'
    else:engine.assert_called_once()


def test_quota_failure_never_retries(monkeypatch):
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *a:{})
    engine=Mock();monkeypatch.setattr(workflow,'execute_candidate',engine)
    posts=[]
    def handle(req):posts.append(req);return httpx.Response(429,json={'error':{'code':'quota'}})
    monkeypatch.setattr(workflow,'request_suggestion',lambda context,config,**kw:request_suggestion(context,config,transport=httpx.MockTransport(handle),**kw))
    r=workflow.run({'QWEN_API_KEY':'unit-test-secret','QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1','QWEN_MODEL':'requested'},use_qwen=True)
    assert len(posts)==r['model_requests']==1
    assert r['status']=='quota_or_rate_limit' and r['stage']=='model_request'
    assert r['recommendation']['decision']=='not_evaluated';engine.assert_not_called()


def test_sampler_30_interleaved_pairs_and_warmups(monkeypatch):
    from sql_optimization_agent.tools import sql_benchmark as runner
    order=[]
    def explain(c,q,params=None):order.append(q);return {'execution_ms':2 if q=='o' else 1,'nodes':[]}
    monkeypatch.setattr(runner,'_explain',explain)
    result=measure_pair(object(),'r',original_sql='o',samples_per_query=30)
    assert order==['o','r']*31
    assert result['samples_per_query']==30
    assert len(result['execution_ms']['original'])==len(result['execution_ms']['rewritten'])==30
    with pytest.raises(ValueError):measure_pair(object(),'r',samples_per_query=True)


def test_wrong_inner_rejected_before_measurement(monkeypatch):
    connection=MagicMock();connection.execute.return_value.fetchone.return_value=('db','reader','on','repeatable read')
    monkeypatch.setattr(workflow,'readonly_connection',lambda c:nullcontext(connection))
    monkeypatch.setattr(workflow,'check_isolation',lambda c:None)
    columns=[('customer_id',23),('order_count',20),('revenue',1700),('last_order_at',1114)]
    results=[QueryResult(columns,Counter({(1,0,None,None):1,(2,2,None,datetime(2025,6,15)):1}),2,100),
             QueryResult(columns,Counter({(2,2,None,datetime(2025,6,15)):1}),1,50)]
    monkeypatch.setattr(workflow,'_fetch',Mock(side_effect=results))
    measure=Mock(side_effect=AssertionError('Wrong candidate reached timing'));monkeypatch.setattr(workflow,'measure_pair',measure)
    r=workflow.finish(workflow.execute_candidate({},wrong_inner_control=True))
    assert r['status']=='result_mismatch' and r['benchmark'] is None
    assert r['recommendation']['decision']=='keep_original';measure.assert_not_called()


def test_service_requires_explicit_mode_and_respects_disabled_scenario(monkeypatch):
    engine=Mock();monkeypatch.setattr(workflow,'run',engine)
    assert OptimizationService({},enabled_scenarios=(workflow.SCENARIO,)).optimize(workflow.SCENARIO)['status']=='unsupported_mode'
    assert OptimizationService({},enabled_scenarios=()).optimize(workflow.SCENARIO,use_qwen=True)['status']=='unsupported_scenario'
    engine.assert_not_called()


def test_cli_forwards_scope_and_config(monkeypatch,tmp_path,capsys):
    p=tmp_path/'local.env';p.write_text('POSTGRES_PORT=12345\n')
    engine=Mock(return_value={'status':'no_rewrite','model_requests':0})
    monkeypatch.setattr(workflow,'run',engine)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--scenario',workflow.SCENARIO,'--fixture','customer-no-rewrite','--customer-scope','narrow','--config-file',str(p)])
    script=runpy.run_path('scripts/optimize_orders.py')
    assert script['main']()==0
    assert engine.call_args.kwargs=={'scope':'narrow','fixture':'customer-no-rewrite','use_qwen':False}
    assert json.loads(capsys.readouterr().out)['status']=='no_rewrite'


@pytest.fixture
def live_config(monkeypatch,block_external_services):
    import os
    path=os.environ.get('CUSTOMER_METRICS_TEST_ENV')
    if not path:pytest.skip('Set CUSTOMER_METRICS_TEST_ENV for isolated customer metrics SQL tests')
    class Secret(str):
        def __repr__(self):return '<redacted>'
    class Config(dict):
        def __repr__(self):return '<isolated customer metrics config; credentials redacted>'
    config=Config(dotenv_values(path,interpolate=False))
    for key in config:
        if 'PASSWORD' in key or key=='QWEN_API_KEY':config[key]=Secret(config[key] or '')
    def connect(*args,**kwargs):
        assert not args and kwargs['host']==config['POSTGRES_HOST'] and kwargs['port']==int(config['POSTGRES_PORT'])
        assert kwargs['user']=='sql_optimization_reader' and kwargs['dbname']=='sql_optimization'
        c=_REAL_CONNECT(**kwargs);workflow.check_isolation(c);c.commit();return c
    monkeypatch.setattr(psycopg,'connect',connect)
    return config


def test_postgres_full_customer_multiset_and_sentinels(live_config):
    from sql_optimization_agent.db import readonly_connection
    op,cp=workflow.parameters('all')
    with readonly_connection(live_config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        left=_fetch(c,workflow.ORIGINAL,120000,64*1024*1024,op)
        right=_fetch(c,workflow.CANDIDATE,120000,64*1024*1024,cp)
        assert _compare(left,right)['equivalent'] and left.count==100004
        assert workflow.sentinel_check(c,workflow.ORIGINAL,op)['passed']
        assert workflow.sentinel_check(c,workflow.CANDIDATE,cp)['passed']


def test_postgres_narrow_and_wrong_inner_semantics(live_config):
    from sql_optimization_agent.db import readonly_connection
    op,cp=workflow.parameters('narrow')
    with readonly_connection(live_config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        a=_fetch(c,workflow.ORIGINAL,120000,64*1024*1024,op)
        b=_fetch(c,workflow.CANDIDATE,120000,64*1024*1024,cp)
        assert _compare(a,b)['equivalent'] and a.count==1
        op,cp=workflow.parameters('all')
        a=_fetch(c,workflow.ORIGINAL,120000,64*1024*1024,op)
        b=_fetch(c,workflow.WRONG_INNER,120000,64*1024*1024,cp)
        assert not _compare(a,b)['equivalent'] and a.count==100004 and b.count==100003
