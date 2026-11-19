"""Matrix policy, durable partial results, timing and zero-live-by-default checks."""

import json
from unittest.mock import Mock

import httpx
import pytest

from sql_optimization_agent.batch_evaluation import evaluate,matrix,SCENARIOS
from sql_optimization_agent.qwen_client import QwenFailure,request_suggestion


def outcome(scenario,status='completed',requests=0,repeat=1):
    action='date_range' if scenario==SCENARIOS[0] else 'exists'
    report={'status':status,'stage':'complete','model_requests':requests,'model_request_ms':12.5 if requests else 0,
            'source':'unit-test','model_id':'test-model','suggestion':{'action':action,'reason':str(repeat)},
            'comparison':{'equivalent':True,'original_rows':0,'candidate_rows':0},'benchmark':None}
    if status=='completed':
        report['benchmark']={'execution_ms':{'original':[1]*7,'rewritten':[2]*7},
                             'median_ms':{'original':1,'rewritten':2},
                             'samples':{name:[{'nodes':[{'Node Type':'Seq Scan'}]}]*7 for name in ('original','rewritten')}}
    return report


def test_default_complete_matrix_is_simulated_and_preserves_repeats(tmp_path):
    calls=[]
    def factory(config,*,fixture):
        assert fixture in ('normal','items-exists')
        class Service:
            def optimize(self,scenario,*,date,use_qwen):
                calls.append((scenario,date,use_qwen))
                return outcome(scenario,repeat=len(calls))
        return Service()
    path=tmp_path/'matrix.json'
    report=evaluate({},path,service_factory=factory)
    assert len(matrix())==len(calls)==12
    assert report['summary']['model_requests']==0
    assert report['summary']['verified_rewrites']==12
    assert report['summary']['actions']=={'date_range':6,'exists':6}
    assert all(g['suggestion_changed'] for g in report['summary']['repeated_groups'])
    assert all(not g['validation_changed'] for g in report['summary']['repeated_groups'])
    assert all(r['model_request_ms']==0 for r in report['results'])
    assert {r['date'] for r in report['results']}=={'2025-06-15','2025-06-16','2026-01-01'}
    plans=report['results'][0]['benchmark']['plan_variants']['original']
    assert plans[0]['sample_numbers']==list(range(1,8))
    assert json.loads(path.read_text())['status']=='completed'


def test_keep_original_never_reports_candidate_timing(tmp_path):
    def factory(config,*,fixture):
        class Service:
            def optimize(self,scenario,**kwargs):
                status='no_suggestion' if scenario==SCENARIOS[0] else 'no_rewrite'
                report=outcome(scenario,status)
                report['suggestion']={'action':status,'reason':'Keep original'}
                return report
        return Service()
    report=evaluate({},tmp_path/'keep.json',service_factory=factory)
    assert report['summary']['kept_original']==12
    assert all(r['decision']=='keep_original' and r['benchmark'] is None for r in report['results'])
    assert all(r['validation']['status']=='not_run_keep_original' for r in report['results'])


@pytest.mark.parametrize('failure',['authentication_error','quota_exceeded','quota_or_rate_limit'])
def test_auth_or_quota_stops_and_saves_partial_results(tmp_path,failure):
    calls=[]
    def factory(config,*,fixture):
        assert fixture is None
        class Service:
            def optimize(self,scenario,**kwargs):
                calls.append(scenario)
                result=outcome(scenario,failure if len(calls)==3 else 'completed',requests=1)
                if len(calls)==3: result.update(stage='model_request',reason='secret-key-value',suggestion=None)
                return result
        return Service()
    path=tmp_path/'partial.json'
    report=evaluate({'QWEN_API_KEY':'secret-key-value'},path,live=True,service_factory=factory)
    assert len(calls)==report['summary']['model_requests']==3
    assert report['status']=='stopped' and report['stop_reason']==failure
    assert 'secret-key-value' not in path.read_text()
    assert json.loads(path.read_text())['summary']['completed_cases']==3


def test_live_matrix_has_twelve_calls_at_most(tmp_path):
    calls=[]
    def factory(config,*,fixture):
        assert fixture is None
        class Service:
            def optimize(self,scenario,**kwargs):
                calls.append(scenario)
                return outcome(scenario,requests=1)
        return Service()
    report=evaluate({},tmp_path/'live.json',live=True,service_factory=factory)
    assert report['status']=='completed' and len(calls)==report['summary']['model_requests']==12


def test_existing_report_prevents_any_service_calls(tmp_path):
    path=tmp_path/'exists.json';path.write_text('preserve')
    factory=Mock(side_effect=AssertionError('No calls allowed'))
    with pytest.raises(FileExistsError): evaluate({},path,live=True,service_factory=factory)
    assert path.read_text()=='preserve'
    factory.assert_not_called()


def test_http_request_timing_is_not_database_timing(monkeypatch):
    values=iter([10.0,10.125])
    monkeypatch.setattr('sql_optimization_agent.qwen_client.perf_counter',lambda:next(values))
    reply=request_suggestion({}, {'QWEN_API_KEY':'unit','QWEN_MODEL':'test',
                                 'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'},
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
            'model':'test','choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})))
    assert reply.request_elapsed_ms==125


def test_failed_http_request_also_has_timing(monkeypatch):
    values=iter([10.0,10.25])
    monkeypatch.setattr('sql_optimization_agent.qwen_client.perf_counter',lambda:next(values))
    with pytest.raises(QwenFailure) as error:
        request_suggestion({}, {'QWEN_API_KEY':'unit','QWEN_MODEL':'test',
                                'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'},
                           transport=httpx.MockTransport(lambda request:httpx.Response(401)))
    assert error.value.request_elapsed_ms==250
