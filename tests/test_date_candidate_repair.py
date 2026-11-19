"""Explicit, bounded repair gate; CI never contacts a model or database."""

from collections import Counter
from contextlib import nullcontext
import json
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from sql_optimization_agent.qwen_client import QwenFailure, request_suggestion
from sql_optimization_agent.qwen_workflow import fixture_content
from sql_optimization_agent.service import OptimizationService, ORDERS_DATE_SCENARIO
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import QueryResult

CONTEXT = OrderDateContext.parse('2025-06-15')
PREFIX = 'sql_optimization_agent.qwen_workflow.'
GOOD = QueryResult([], Counter({('synthetic',): 1}), 1, 1)
BAD = QueryResult([], Counter({('synthetic',): 1, ('private-row',): 1}), 2, 2)
CONFIG = {'QWEN_API_KEY':'test-key-not-real','QWEN_MODEL':'test-model',
          'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(PREFIX+'diagnostic_context', lambda *args: {'date_context':CONTEXT.as_dict()})
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ('sql_optimization','sql_optimization_reader','on','repeatable read','test')
    connection = Mock(side_effect=lambda _: nullcontext(conn))
    monkeypatch.setattr(PREFIX+'readonly_connection', connection)
    fetch = Mock(side_effect=[GOOD,BAD,GOOD,GOOD])
    monkeypatch.setattr(PREFIX+'_fetch', fetch)
    measure = Mock(return_value={'execution_ms':{'original':[10]*7,'rewritten':[1]*7},'samples_per_query':7})
    monkeypatch.setattr(PREFIX+'measure_pair', measure)
    return fetch,measure,connection


def run(fixture=None,repair=True,config=None):
    return OptimizationService(CONFIG if config is None else config,fixture=fixture).optimize(
        ORDERS_DATE_SCENARIO,repair_on_mismatch=repair)


def test_repair_success_only_measures_after_validation(harness):
    fetch,measure,connections = harness
    def measured(*args,**kwargs):
        assert fetch.call_count == 4
        assert '<= %s' not in args[1]
        return {'execution_ms':{'original':[10]*7,'rewritten':[1]*7},'samples_per_query':7}
    measure.side_effect = measured
    report = run('repair-success')
    assert [a['status'] for a in report['attempts']] == ['result_mismatch','completed']
    assert report['attempts'][0]['benchmark'] is None
    assert report['recommendation']['decision'] == 'use_candidate'
    assert report['model_requests'] == 0 and connections.call_count == 2
    measure.assert_called_once()
    feedback = json.dumps(report['repair_feedback'])
    assert 'private-row' not in feedback and 'synthetic' not in feedback
    assert report['repair_feedback']['difference_summary']['extra_row_copies'] == 1


def test_failed_repair_keeps_original_without_timing(harness):
    fetch,measure,_ = harness
    fetch.side_effect = [GOOD,BAD,GOOD,BAD]
    report = run('repair-failure')
    assert [a['status'] for a in report['attempts']] == ['result_mismatch']*2
    assert report['recommendation'] == {'decision':'keep_original','reason_code':'candidate_rejected','advisory_only':True}
    assert report['benchmark'] is None
    measure.assert_not_called()


@pytest.mark.parametrize('fixture', ['repair-success','wrong-upper'])
def test_disabled_flag_never_repairs(harness,fixture):
    fetch,measure,_ = harness
    report = run(fixture,repair=False)
    assert len(report['attempts']) == 1 and fetch.call_count == 2
    assert 'repair_feedback' not in report
    assert report['max_model_requests'] == 1
    measure.assert_not_called()


@pytest.mark.parametrize('fixture,status', [('no-suggestion','no_suggestion'),('malformed','invalid_suggestion')])
def test_retained_or_invalid_suggestion_never_repairs(harness,fixture,status):
    fetch,measure,_ = harness
    report = run(fixture)
    assert report['status'] == status and len(report['attempts']) == 1
    assert report['recommendation']['decision'] == 'keep_original'
    fetch.assert_not_called();measure.assert_not_called()


@pytest.mark.parametrize('failure', ['missing_config','authentication_error','network_error','quota_exceeded'])
def test_request_failure_does_not_trigger_repair(harness,monkeypatch,failure):
    request = Mock(side_effect=QwenFailure(failure,'sanitized failure'))
    monkeypatch.setattr(PREFIX+'request_suggestion',request)
    report = run()
    assert report['status'] == failure and len(report['attempts']) == 1
    assert report['recommendation']['decision'] == 'not_evaluated'
    request.assert_called_once()
    assert report['model_requests'] == (0 if failure=='missing_config' else 1)


@pytest.mark.parametrize('second', ['normal','wrong-upper','malformed','no-suggestion'])
def test_live_mode_transport_has_at_most_two_posts(harness,monkeypatch,second):
    fetch,measure,connections = harness
    if second == 'wrong-upper':fetch.side_effect = [GOOD,BAD,GOOD,BAD]
    requests=[]
    def handle(request):
        # No transaction remains open while the second model request is made.
        assert connections.call_count == (0 if not requests else 1)
        payload=json.loads(request.content)
        requests.append(payload)
        if len(requests)==2:
            context=json.loads(payload['messages'][1]['content'])
            assert context['repair_feedback']['target_date']=='2025-06-15'
            assert 'private-row' not in json.dumps(payload)
        name='wrong-upper' if len(requests)==1 else second
        return httpx.Response(200,json={'model':'test-model','choices':[
            {'finish_reason':'stop','message':{'content':fixture_content(name,CONTEXT)}}]})
    transport=httpx.MockTransport(handle)
    monkeypatch.setattr(PREFIX+'request_suggestion',lambda context,config:request_suggestion(context,config,transport=transport))
    report=run()
    assert len(requests)==report['model_requests']==2
    assert [a['model_requests'] for a in report['attempts']]==[1,1]
    assert measure.call_count == (1 if second=='normal' else 0)
    assert report['recommendation']['decision']==('use_candidate' if second=='normal' else 'keep_original')


def test_first_valid_candidate_does_not_request_repair(harness,monkeypatch):
    fetch,measure,_=harness
    fetch.side_effect=[GOOD,GOOD]
    report=run('normal')
    assert len(report['attempts'])==1 and report['status']=='completed'
    assert 'repair_feedback' not in report
    measure.assert_called_once()


def test_repair_service_failure_preserves_first_attempt(harness,monkeypatch):
    from sql_optimization_agent.qwen_client import ModelReply
    request=Mock(side_effect=[ModelReply('test',fixture_content('wrong-upper',CONTEXT)),
                             QwenFailure('model_timeout','sanitized timeout')])
    monkeypatch.setattr(PREFIX+'request_suggestion',request)
    report=run()
    assert report['status']=='model_timeout' and report['stage']=='model_request'
    assert report['attempts'][0]['status']=='result_mismatch'
    assert report['attempts'][1]['status']=='model_timeout'
    assert 'comparison' not in report and report['benchmark'] is None
    assert 'suggestion' not in report and 'received_content' not in report and 'model_id' not in report
    assert report['model_requests']==2
    assert report['recommendation']['decision']=='not_evaluated'


def test_missing_configuration_uses_existing_validation(harness):
    report=run(config={})
    assert report['status']=='missing_config' and report['model_requests']==0
    assert len(report['attempts'])==1


def test_other_scenario_rejects_repair_before_work(harness):
    report=OptimizationService({}).optimize('orders_with_items_count',repair_on_mismatch=True)
    assert report['status']=='unsupported_mode' and report['model_requests']==0
    harness[0].assert_not_called()


@pytest.mark.parametrize('failure', ['configuration','budget'])
def test_database_failure_never_repairs(harness,monkeypatch,failure):
    from sql_optimization_agent.db import DatabaseConfigurationError
    from sql_optimization_agent.tools.sql_benchmark import BenchmarkValidationError
    error=DatabaseConfigurationError('Missing database variable') if failure=='configuration' else BenchmarkValidationError('Fetch budget exceeded')
    harness[0].side_effect=error
    report=run('repair-success')
    assert report['status']=='validation_error' and len(report['attempts'])==1
    assert 'repair_feedback' not in report
    harness[1].assert_not_called()


@pytest.mark.parametrize('times,verdict',[({'original':[10]*7,'rewritten':[20]*7},'regressed'),
                                          ({'original':[1,2,3,4,5,6,7],'rewritten':[1,1,2,2,3,3,7]},'inconclusive')])
def test_repair_success_still_uses_final_performance_rules(harness,times,verdict):
    harness[1].return_value={'execution_ms':times,'samples_per_query':7}
    report=run('repair-success')
    assert report['performance_assessment']['verdict']==verdict
    assert report['recommendation']['decision']=='keep_original'
    assert report['recommendation']['reason_code']=='performance_'+verdict


def test_cli_forwards_explicit_repair_flag(monkeypatch,capsys):
    import runpy,sys
    from pathlib import Path
    service=Mock()
    service.optimize.return_value={'status':'completed'}
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',lambda **kwargs:service)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--fixture','repair-success','--repair-on-mismatch'])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==0
    service.optimize.assert_called_once_with(ORDERS_DATE_SCENARIO,date=None,use_qwen=False,repair_on_mismatch=True)
    assert json.loads(capsys.readouterr().out)['status']=='completed'


def test_live_transport_without_flag_stops_after_first_mismatch(harness,monkeypatch):
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'model':'test-model','choices':[
            {'finish_reason':'stop','message':{'content':fixture_content('wrong-upper',CONTEXT)}}]})
    monkeypatch.setattr(PREFIX+'request_suggestion',lambda context,config:request_suggestion(context,config,transport=httpx.MockTransport(handle)))
    report=run(repair=False)
    assert report['status']=='result_mismatch'
    assert len(requests)==report['model_requests']==1
    harness[1].assert_not_called()
