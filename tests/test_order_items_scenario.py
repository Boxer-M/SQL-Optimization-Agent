"""Shared-service/CLI dispatch and both semantic gates for controlled item counts."""

from collections import Counter
from contextlib import nullcontext
from datetime import date
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock,Mock

import pytest

from sql_optimization_agent.service import DEFAULT_ORDER_DATE, ORDER_ITEMS_SCENARIO, ORDERS_DATE_SCENARIO, OptimizationService, configured_service
from sql_optimization_agent.tools import order_items_benchmark as items
from sql_optimization_agent.tools.sql_benchmark import QueryResult


@pytest.fixture
def offline_items(monkeypatch):
    connection=MagicMock()
    connection.execute.return_value.fetchone.side_effect=[
        ('sql_optimization','sql_optimization_reader','on','repeatable read','controlled'),
        (20,), (3,1,1,1,4), (2,), (2,)]
    monkeypatch.setattr(items,'readonly_connection',lambda config:nullcontext(connection))
    result=QueryResult([('order_id',20)],Counter({(10,):1,(20,):1}),2,100)
    fetch=Mock(return_value=result)
    measure=Mock(return_value={'median_ms':{'original':1.0,'rewritten':1.1}})
    monkeypatch.setattr(items,'_fetch',fetch)
    monkeypatch.setattr(items,'measure_pair',measure)
    model=Mock(side_effect=AssertionError('Items scenario must never call Qwen'))
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.request_suggestion',model)
    return connection,fetch,measure,model


@pytest.mark.parametrize('target,expected',[(None,DEFAULT_ORDER_DATE),('2025-06-16','2025-06-16')])
def test_service_uses_existing_date_context_and_database_counts(offline_items,target,expected):
    connection,fetch,measure,model=offline_items
    report=OptimizationService({}).optimize(ORDER_ITEMS_SCENARIO,date=target)
    assert report['status']=='completed'
    assert report['target_date']==expected
    assert report['counts']=={'join_count_distinct':2,'candidate_count':2}  # never hardcode 551
    assert report['membership']['join_minus_candidate_count']==report['membership']['candidate_minus_join_count']==0
    assert fetch.call_args_list[0].args[4][0].date()==date.fromisoformat(expected)
    assert measure.call_args.kwargs['original_sql']==items.JOIN_COUNT
    assert measure.call_args.kwargs['original_params']==fetch.call_args_list[0].args[4]
    assert report['model_requests']==0
    model.assert_not_called()


def test_count_mismatch_also_blocks_measurement(offline_items):
    connection,fetch,measure,model=offline_items
    connection.execute.return_value.fetchone.side_effect=[
        ('db','reader','on','repeatable read','test'),(20,),(3,1,1,1,4),(2,),(3,)]
    report=OptimizationService({}).optimize(ORDER_ITEMS_SCENARIO)
    assert report['status']=='count_mismatch' and report['benchmark'] is None
    assert report['membership']['comparison']['equivalent']
    measure.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize('fixture',[None,'swapped-ids'])
def test_cli_dispatches_items_scenario(offline_items,monkeypatch,capsys,fixture):
    _,fetch,_,model=offline_items
    if fixture:
        fetch.side_effect=[QueryResult([('order_id',20)],Counter({(10,):1,(20,):1}),2,100),
                           QueryResult([('order_id',20)],Counter({(10,):1,(30,):1}),2,100)]
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',lambda **kwargs:OptimizationService({},**kwargs))
    args=['optimize_orders.py','--scenario',ORDER_ITEMS_SCENARIO,'--date','2025-06-15']
    if fixture: args += ['--fixture',fixture]
    monkeypatch.setattr(sys,'argv',args)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/optimize_orders.py'),run_name='__main__')
    report=json.loads(capsys.readouterr().out)
    assert stopped.value.code==(1 if fixture else 0)
    assert report['status']==('membership_mismatch' if fixture else 'completed')
    if fixture:
        assert report['counts']['join_count_distinct']==report['counts']['candidate_count']==2
        assert report['benchmark'] is None
    model.assert_not_called()


def test_invalid_items_date_stops_before_database(monkeypatch):
    run=Mock()
    monkeypatch.setattr('sql_optimization_agent.service.run_items',run)
    report=OptimizationService({}).optimize(ORDER_ITEMS_SCENARIO,date='2025-02-29')
    assert report['status']=='invalid_date' and report['model_requests']==0
    run.assert_not_called()


def test_http_mcp_default_service_keeps_existing_scope(monkeypatch):
    run=Mock()
    monkeypatch.setattr('sql_optimization_agent.service.run_items',run)
    service=configured_service()
    assert service.enabled_scenarios==(ORDERS_DATE_SCENARIO,)
    assert service.optimize(ORDER_ITEMS_SCENARIO)['status']=='unsupported_scenario'
    run.assert_not_called()
