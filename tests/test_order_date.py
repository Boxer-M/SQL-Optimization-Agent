"""Date propagation and calendar-boundary checks without external services."""

from collections import Counter
from contextlib import nullcontext
from datetime import date, datetime
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from sql_optimization_agent.order_date import ORDER_DATE_SQL, OrderDateContext
from sql_optimization_agent.qwen_client import request_suggestion
from sql_optimization_agent.qwen_workflow import diagnostic_context, fixture_content, parse_suggestion, run_workflow
from sql_optimization_agent.service import DEFAULT_ORDER_DATE, ORDERS_DATE_SCENARIO, OptimizationService
from sql_optimization_agent.tools.sql_benchmark import QueryResult, measure_pair


@pytest.mark.parametrize('target,end', [('2025-01-31','2025-02-01'), ('2025-12-31','2026-01-01'),
                                      ('2024-02-28','2024-02-29'), ('2024-02-29','2024-03-01')])
def test_calendar_boundaries(target, end):
    context = OrderDateContext.parse(target)
    assert context.start == datetime.fromisoformat(target)
    assert context.end == datetime.fromisoformat(end)
    assert context.start.tzinfo is context.end.tzinfo is None
    value, candidate = parse_suggestion(fixture_content('normal', context), context)
    assert candidate.params == (context.start, context.end)
    assert value['end'] == end + ' 00:00:00'


@pytest.mark.parametrize('value', ['', '2025-6-15', '2025-02-29', '2025-13-01', '2025-06-15T00:00:00',
                                 '20250615', ' 2025-06-15', '9999-12-31', '0000-01-01', 'not-a-date'])
def test_invalid_date_stops_before_workflow(monkeypatch, value):
    workflow = Mock(side_effect=AssertionError('Invalid date must not invoke workflow/model'))
    monkeypatch.setattr('sql_optimization_agent.service.run_workflow', workflow)
    report = OptimizationService({}).optimize(ORDERS_DATE_SCENARIO, date=value)
    assert report['status'] == 'invalid_date'
    assert report['stage'] == 'configuration'
    assert report['model_requests'] == 0
    assert 'YYYY-MM-DD' in report['reason']
    workflow.assert_not_called()


def test_default_and_explicit_date_share_one_service_default(monkeypatch):
    workflow = Mock(return_value={'status': 'completed'})
    monkeypatch.setattr('sql_optimization_agent.service.run_workflow', workflow)
    service = OptimizationService({})
    assert service.optimize(ORDERS_DATE_SCENARIO) == service.optimize(ORDERS_DATE_SCENARIO, date=DEFAULT_ORDER_DATE)
    assert workflow.call_args_list[0].kwargs['date_context'] == workflow.call_args_list[1].kwargs['date_context']


def test_model_prompt_receives_selected_date():
    context = OrderDateContext.parse('2025-06-16')
    observed = []
    def handle(request):
        payload = json.loads(request.content)
        observed.append(payload)
        return httpx.Response(200, json={'model': 'controlled', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': fixture_content('normal', context)}}]})
    request_suggestion({'date_context': context.as_dict(), 'original_sql': ORDER_DATE_SQL},
                       {'QWEN_API_KEY':'unit-secret-not-real', 'QWEN_MODEL':'controlled',
                        'QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'},
                       transport=httpx.MockTransport(handle))
    assert '2025-06-16 00:00:00' in observed[0]['messages'][0]['content']
    assert '2025-06-17 00:00:00' in observed[0]['messages'][0]['content']
    assert '2025-06-15' not in observed[0]['messages'][0]['content']


def test_candidate_for_other_date_is_rejected():
    previous = OrderDateContext.parse('2025-06-15')
    current = OrderDateContext.parse('2025-06-16')
    from sql_optimization_agent.qwen_client import QwenFailure
    with pytest.raises(QwenFailure, match='Unsupported date range'):
        parse_suggestion(fixture_content('normal',previous), current)


def test_diagnosis_comparison_and_measurement_receive_same_original(monkeypatch):
    context = OrderDateContext.parse('2025-06-16')
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ([{'Plan': {'Node Type':'Seq Scan'}}],)
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.inspect_table', lambda *args: {'columns': [
        {'name':n, 'data_type':t} for n,t in [('customer_id','integer'), ('created_at','timestamp without time zone'),
                                           ('status','text'), ('amount','numeric(12,2)')]]})
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.readonly_connection', lambda _: nullcontext(connection))
    diagnostic = diagnostic_context({}, context)
    connection.execute.assert_any_call('EXPLAIN (FORMAT JSON) '+ORDER_DATE_SQL, (date(2025,6,16),))
    assert diagnostic['original_parameters'] == ['2025-06-16']
    connection.execute.return_value.fetchone.return_value = ('sql_optimization','sql_optimization_reader','on','repeatable read','test')
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.diagnostic_context', lambda *args: diagnostic)
    fetch = Mock(return_value=QueryResult([], Counter(), 0, 0))
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow._fetch', fetch)
    measure = Mock(return_value={})
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.measure_pair', measure)
    report = run_workflow({}, date_context=context, fixture='normal')
    assert report['target_date'] == '2025-06-16'
    assert fetch.call_args_list[0].args[1] == ORDER_DATE_SQL
    assert fetch.call_args_list[0].args[4] == (date(2025,6,16),)
    assert measure.call_args.kwargs == {'original_sql':ORDER_DATE_SQL, 'original_params':(date(2025,6,16),)}
    explain = Mock(return_value={'execution_ms':1, 'planning_ms':0, 'nodes':[]})
    monkeypatch.setattr('sql_optimization_agent.tools.sql_benchmark._explain', explain)
    measure_pair(connection, report['candidate']['sql'], (context.start, context.end),
                 original_sql=ORDER_DATE_SQL, original_params=context.original_params)
    originals = [call for call in explain.call_args_list if call.args[1] == ORDER_DATE_SQL]
    assert len(originals) == 8  # one warmup plus seven measured runs
    assert all(call.args[2] == (date(2025,6,16),) for call in originals)


def test_wrong_upper_reaches_result_comparison_and_skips_measurement(monkeypatch):
    context = OrderDateContext.parse(DEFAULT_ORDER_DATE)
    _, candidate = parse_suggestion(fixture_content('wrong-upper', context), context)
    assert '<= %s' in candidate.sql  # structurally supported; semantic gate is full result comparison
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.diagnostic_context', lambda *args: {})
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.readonly_connection', lambda _: nullcontext(MagicMock()))
    fetch = Mock(side_effect=[QueryResult([],Counter({(1,):1}),1,1), QueryResult([],Counter({(1,):1,(2,):1}),2,2)])
    measure = Mock()
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow._fetch', fetch)
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.measure_pair', measure)
    report = run_workflow({}, date_context=context, fixture='wrong-upper')
    assert report['stage'] == 'result_validation' and report['status'] == 'result_mismatch'
    assert report['benchmark'] is None
    measure.assert_not_called()


@pytest.mark.parametrize('target,expected,exit_code', [(None,DEFAULT_ORDER_DATE,0), ('2025-06-16','2025-06-16',0),
                                                    ('2025-02-29',None,1)])
def test_cli_date_argument(monkeypatch, capsys, target, expected, exit_code):
    service = OptimizationService({}, fixture='normal')
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService', lambda **kwargs: service)
    workflow = Mock(side_effect=lambda config, *, date_context, fixture: {
        'status':'completed', 'target_date':date_context.target.isoformat()})
    monkeypatch.setattr('sql_optimization_agent.service.run_workflow', workflow)
    args = ['optimize_orders.py','--fixture','normal'] + (['--date',target] if target is not None else [])
    monkeypatch.setattr(sys,'argv',args)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code == exit_code
    report = json.loads(capsys.readouterr().out)
    if expected:
        assert report['target_date'] == expected
    else:
        assert report['status'] == 'invalid_date'
        workflow.assert_not_called()
