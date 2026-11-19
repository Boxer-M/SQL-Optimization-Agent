"""Fixed initial candidate never consumes a real model request."""

from collections import Counter
from contextlib import nullcontext
import json
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from sql_optimization_agent.qwen_client import request_suggestion
from sql_optimization_agent.qwen_workflow import fixture_content
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.repair_evaluation import evaluate
from sql_optimization_agent.tools.sql_benchmark import QueryResult

PREFIX='sql_optimization_agent.qwen_workflow.'
CONFIG={'QWEN_API_KEY':'not-real-secret','QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'}
CONTEXT=OrderDateContext.parse('2025-06-15')
GOOD=QueryResult([],Counter({('synthetic-row',):825}),825,1)
BAD=QueryResult([],Counter({('synthetic-row',):825,('not-for-prompt',):1}),826,1)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(PREFIX+'diagnostic_context',lambda *args:{'date_context':CONTEXT.as_dict()})
    connection=MagicMock()
    connection.execute.return_value.fetchone.return_value=('sql_optimization','sql_optimization_reader','on','repeatable read','test')
    monkeypatch.setattr(PREFIX+'readonly_connection',lambda _:nullcontext(connection))
    fetch=Mock(side_effect=[GOOD,BAD,GOOD,GOOD]*3)
    monkeypatch.setattr(PREFIX+'_fetch',fetch)
    measure=Mock(return_value={'execution_ms':{'original':[10]*7,'rewritten':[1]*7},'samples_per_query':7})
    monkeypatch.setattr(PREFIX+'measure_pair',measure)
    return fetch,measure


def test_default_simulates_all_repairs_without_model_requests(setup,monkeypatch,tmp_path):
    request=Mock(side_effect=AssertionError('No real model'))
    monkeypatch.setattr(PREFIX+'request_suggestion',request)
    output=tmp_path/'report.json'
    report=evaluate(CONFIG,output)
    assert report['summary']=={'executed_cases':3,'real_model_requests':0,'passed_cases':3,'timed_cases':3}
    assert all([a['source'] for a in row['attempts']]==['fixed_wrong_upper','controlled_model_response'] for row in report['results'])
    assert setup[1].call_count==3
    request.assert_not_called()
    assert CONFIG['QWEN_API_KEY'] not in output.read_text()


def test_live_has_one_post_per_case_and_only_aggregate_feedback(setup,monkeypatch,tmp_path):
    requests=[]
    def handle(request):
        payload=json.loads(request.content);requests.append(payload)
        assert payload['model']=='qwen3.7-plus'
        context=json.loads(payload['messages'][1]['content'])
        feedback=context['repair_feedback']
        assert feedback['difference_summary']['original_rows']==825
        assert feedback['difference_summary']['candidate_rows']==826
        assert feedback['candidate_boundaries']['end_inclusive'] is True
        assert 'not-for-prompt' not in json.dumps(payload)
        assert 'not-real-secret' not in json.dumps(payload)
        return httpx.Response(200,json={'model':'qwen3.7-plus','choices':[
            {'finish_reason':'stop','message':{'content':fixture_content('normal',CONTEXT)}}]})
    monkeypatch.setattr(PREFIX+'request_suggestion',lambda context,config:request_suggestion(context,config,transport=httpx.MockTransport(handle)))
    report=evaluate(CONFIG,tmp_path/'live.json',live=True)
    assert len(requests)==report['summary']['real_model_requests']==3
    assert report['summary']['passed_cases']==3
    for row in report['results']:
        assert row['model_requests']==row['max_model_requests']==1
        assert [a['model_requests'] for a in row['attempts']]==[0,1]
        assert row['attempt_labels']==['首轮固定候选','第二轮真实千问']
        assert row['attempts'][0]['benchmark'] is None


@pytest.mark.parametrize('status,body,expected',[(401,{},'authentication_error'),(429,{},'quota_or_rate_limit'),
                                               (400,{'error':{'code':'quota'}},'quota_exceeded')])
def test_auth_and_quota_stop_and_save_completed_cases(setup,monkeypatch,tmp_path,status,body,expected):
    calls=[]
    def handle(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'model':'m','choices':[{'finish_reason':'stop','message':{'content':fixture_content('normal',CONTEXT)}}]})
        return httpx.Response(status,json=body)
    monkeypatch.setattr(PREFIX+'request_suggestion',lambda context,config:request_suggestion(context,config,transport=httpx.MockTransport(handle)))
    output=tmp_path/'stopped.json'
    report=evaluate(CONFIG,output,live=True)
    saved=json.loads(output.read_text())
    assert saved['status']==report['status']=='stopped' and saved['stop_reason']==expected
    assert len(calls)==2 and len(saved['results'])==2
    assert saved['summary']['passed_cases']==1
    assert saved['results'][1]['attempts'][0]['comparison']['candidate_rows']==826


def test_precondition_failure_skips_request_and_timing(setup,monkeypatch,tmp_path):
    setup[0].side_effect=[GOOD,GOOD]
    request=Mock();monkeypatch.setattr(PREFIX+'request_suggestion',request)
    report=evaluate(CONFIG,tmp_path/'precondition.json',live=True)
    assert report['status']=='stopped' and report['stop_reason']=='evaluation_precondition_failed'
    assert report['summary']['real_model_requests']==0
    setup[1].assert_not_called();request.assert_not_called()


def test_existing_report_never_overwritten(setup,tmp_path):
    output=tmp_path/'existing.json';output.write_text('preserve')
    with pytest.raises(FileExistsError):evaluate(CONFIG,output,live=True)
    assert output.read_text()=='preserve'
    setup[0].assert_not_called()


def test_interrupted_evaluation_preserves_completed_result(setup,monkeypatch,tmp_path):
    from sql_optimization_agent import repair_evaluation
    original=repair_evaluation.run_workflow
    calls=[]
    def workflow(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise KeyboardInterrupt
        return original(*args,**kwargs)
    monkeypatch.setattr(repair_evaluation,'run_workflow',workflow)
    output=tmp_path/'interrupted.json'
    with pytest.raises(KeyboardInterrupt):evaluate(CONFIG,output)
    saved=json.loads(output.read_text())
    assert saved['status']=='interrupted' and len(saved['results'])==1


@pytest.mark.parametrize('live',[False,True])
def test_cli_requires_explicit_live_flag(monkeypatch,tmp_path,capsys,live):
    import runpy,sys
    from pathlib import Path
    import sql_optimization_agent.repair_evaluation as module
    evaluator=Mock(return_value={'status':'completed','summary':{'passed_cases':3}})
    monkeypatch.setattr(module,'evaluate',evaluator)
    output=tmp_path/'cli.json'
    monkeypatch.setattr(sys,'argv',['evaluate_date_repair.py','--output',str(output)]+(['--live'] if live else []))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/evaluate_date_repair.py'),run_name='__main__')
    assert stopped.value.code==0
    assert evaluator.call_args.kwargs['live'] is live
    assert json.loads(capsys.readouterr().out)['status']=='completed'
