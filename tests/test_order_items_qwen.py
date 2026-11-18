"""Two-action model contract and explicit opt-in without live credentials."""

import json
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock

import httpx
import pytest

from sql_optimization_agent import order_items_qwen as workflow
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.qwen_client import QwenFailure,request_suggestion
from sql_optimization_agent.service import OptimizationService,ORDER_ITEMS_SCENARIO
from sql_optimization_agent.tools import order_items_benchmark as engine
from sql_optimization_agent.tools.sql_benchmark import BenchmarkValidationError


CONTEXT=OrderDateContext.parse('2025-06-15')


@pytest.mark.parametrize('content', ['{}','[]','not-json','{"action":"exists","reason":"x","sql":"SELECT 1"}',
                                   '{"action":"execute_sql","reason":"x"}',
                                   '{"action":"normal","reason":"x"}',
                                   '{"action":"exists","action":"no_rewrite","reason":"x"}',
                                   '{"action":"exists","reason":null}'])
def test_invalid_advice_rejected(content):
    with pytest.raises(QwenFailure): workflow.parse_advice(content)


def test_executor_rejects_everything_except_two_actions(monkeypatch):
    execute=Mock(return_value={'status':'completed'})
    monkeypatch.setattr(engine,'run',execute)
    for action in ['execute_sql','normal','swapped-ids','date_range']:
        with pytest.raises(BenchmarkValidationError): engine.run_action({},CONTEXT,action)
    assert engine.run_action({},CONTEXT,'no_rewrite')['status']=='no_rewrite'
    execute.assert_not_called()
    engine.run_action({},CONTEXT,'exists')
    execute.assert_called_once_with({},CONTEXT,fixture='normal')
    assert engine.EXISTS_COUNT.split('\n',1)[1]==engine.EXISTS_IDS.split('\n',1)[1]==engine.EXISTS_FROM


@pytest.mark.parametrize('fixture,status', [('items-exists','completed'),('items-no-rewrite','no_rewrite'),('items-invalid','invalid_suggestion')])
def test_simulated_paths(fixture,status,monkeypatch):
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *args:{'date_context':CONTEXT.as_dict()})
    action=Mock(side_effect=lambda config,context,value: engine.run_action(config,context,value) if value=='no_rewrite'
                else {'status':'completed','stage':'complete','model_requests':0,'source':'database-only',
                      'counts':{'join_count_distinct':2,'candidate_count':2},'benchmark':{}})
    monkeypatch.setattr(workflow,'run_action',action)
    report=workflow.run({},CONTEXT,fixture=fixture)
    assert report['status']==status and report['model_requests']==0
    assert report['source']=='controlled_model_response_order_items'
    if fixture=='items-invalid':
        assert report['stage']=='suggestion_validation'
        action.assert_not_called()
    if fixture=='items-no-rewrite': assert report['benchmark'] is None


def test_live_mode_uses_existing_client_once_with_items_prompt(monkeypatch):
    context={'original_sql':engine.JOIN_COUNT,'tables':[{'table':'orders'},{'table':'order_items'}],
             'date_context':CONTEXT.as_dict(),'estimated_plan':[{'Node Type':'Aggregate'}]}
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *args:context)
    monkeypatch.setattr(workflow,'run_action',lambda *args:{'status':'completed','stage':'complete','model_requests':0,'benchmark':{}})
    requests=[]
    def handle(request):
        requests.append(request)
        payload=json.loads(request.content)
        assert 'no_rewrite' in payload['messages'][0]['content']
        assert 'date_range' not in payload['messages'][0]['content']
        assert json.loads(payload['messages'][1]['content'])==context
        assert 'unit-test-secret' not in json.dumps(payload)
        return httpx.Response(200,json={'model':'actual-test-model','choices':[
            {'finish_reason':'stop','message':{'content':workflow.MODEL_FIXTURES['items-exists']}}]})
    monkeypatch.setattr(workflow,'request_suggestion',lambda context,config,**kwargs:
                        request_suggestion(context,config,transport=httpx.MockTransport(handle),**kwargs))
    report=workflow.run({'QWEN_API_KEY':'unit-test-secret','QWEN_MODEL':'requested-model',
                         'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'},CONTEXT)
    assert len(requests)==1 and report['model_requests']==1
    assert report['model_id']=='actual-test-model' and report['source']=='live_qwen_order_items'


def test_service_requires_explicit_qwen_opt_in(monkeypatch):
    direct=Mock(return_value={'status':'completed'})
    model=Mock(return_value={'status':'no_rewrite'})
    monkeypatch.setattr('sql_optimization_agent.service.run_items',direct)
    monkeypatch.setattr('sql_optimization_agent.service.run_items_qwen',model)
    service=OptimizationService({})
    service.optimize(ORDER_ITEMS_SCENARIO)
    direct.assert_called_once()
    model.assert_not_called()
    service.optimize(ORDER_ITEMS_SCENARIO,use_qwen=True)
    model.assert_called_once()


def test_cli_explicit_no_rewrite_exits_successfully(monkeypatch,capsys):
    real_service=OptimizationService
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',lambda **kwargs:real_service({},**kwargs))
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *args:{})
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--scenario',ORDER_ITEMS_SCENARIO,'--qwen',
                                    '--fixture','items-no-rewrite','--date','2025-06-15'])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==0
    assert json.loads(capsys.readouterr().out)['status']=='no_rewrite'
