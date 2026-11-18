"""Both adapters exercise the model-action and result gates without external services."""

from collections import Counter
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock,Mock

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from sql_optimization_agent.api import create_app
from sql_optimization_agent.mcp_server import create_server
from sql_optimization_agent.service import ORDER_ITEMS_SCENARIO,OptimizationService,configured_service
from sql_optimization_agent.tools.sql_benchmark import QueryResult


@pytest.fixture
def anyio_backend(): return 'asyncio'


@pytest.fixture
def items_service(monkeypatch):
    from sql_optimization_agent.tools import order_items_benchmark as engine
    from sql_optimization_agent import order_items_qwen as workflow
    context=Mock(return_value={})
    monkeypatch.setattr(workflow,'diagnostic_context',context)
    monkeypatch.setattr(workflow,'request_suggestion',Mock(side_effect=AssertionError('Live model prohibited')))
    connection=MagicMock()
    connection.execute.return_value.fetchone.side_effect=[('db','reader','on','repeatable read','test'),(20,),(3,1,1,1,4),(2,),(2,)]
    monkeypatch.setattr(engine,'readonly_connection',lambda _:nullcontext(connection))
    result=QueryResult([('order_id',20)],Counter({(10,):1,(20,):1}),2,100)
    fetch=Mock(return_value=result)
    measure=Mock(return_value={'median_ms':{'original':1,'rewritten':0.9}})
    monkeypatch.setattr(engine,'_fetch',fetch)
    monkeypatch.setattr(engine,'measure_pair',measure)
    def factory(fixture,mismatch=False):
        if mismatch:
            fetch.side_effect=[result,QueryResult([('order_id',20)],Counter({(10,):1,(30,):1}),2,100)]
        return OptimizationService({},items_mode='qwen',items_fixture=fixture)
    return factory,fetch,measure,context


CASES=[('items-exists',False,'completed'),('items-no-rewrite',False,'no_rewrite'),
       ('items-invalid',False,'invalid_suggestion'),('items-exists',True,'membership_mismatch')]


@pytest.mark.parametrize('fixture,mismatch,status',CASES)
def test_http_items_paths(items_service,fixture,mismatch,status):
    factory,fetch,measure,_=items_service
    with TestClient(create_app(factory(fixture,mismatch))) as client:
        response=client.post('/optimize',json={'scenario':ORDER_ITEMS_SCENARIO})
    assert response.status_code==200
    report=response.json()
    assert report['status']==status and report['target_date']=='2025-06-15'
    assert report['model_requests']==0
    if status!='completed':
        assert report['benchmark'] is None
        measure.assert_not_called()
    if status in ('no_rewrite','invalid_suggestion'): fetch.assert_not_called()
    if mismatch: assert report['counts']['join_count_distinct']==report['counts']['candidate_count']==2


@pytest.mark.anyio
@pytest.mark.parametrize('fixture,mismatch,status',CASES)
async def test_mcp_items_paths(items_service,fixture,mismatch,status):
    factory,fetch,measure,_=items_service
    async with create_connected_server_and_client_session(create_server(factory(fixture,mismatch))) as session:
        tools=await session.list_tools()
        assert set(tools.tools[0].inputSchema['properties'])=={'scenario','date'}
        result=await session.call_tool('optimize_orders_date',{'scenario':ORDER_ITEMS_SCENARIO})
    report=result.structuredContent
    assert report['status']==status and report['target_date']=='2025-06-15'
    assert result.isError==(status not in ('completed','no_rewrite'))
    if status!='completed':
        assert report['benchmark'] is None
        measure.assert_not_called()
    if status in ('no_rewrite','invalid_suggestion'): fetch.assert_not_called()


def test_http_explicit_date_and_operator_configuration(items_service,monkeypatch):
    factory,_,measure,context=items_service
    with TestClient(create_app(factory('items-exists'))) as client:
        result=client.post('/optimize',json={'scenario':ORDER_ITEMS_SCENARIO,'date':'2025-06-16'})
        for key,value in [('use_qwen',True),('fixture','items-exists'),('sql','SELECT 1')]:
            assert client.post('/optimize',json={'scenario':ORDER_ITEMS_SCENARIO,key:value}).status_code==422
    assert result.json()['target_date']=='2025-06-16'
    assert context.call_args.args[1].target==date(2025,6,16)
    assert measure.call_args.kwargs['original_params'][0].date()==date(2025,6,16)
    monkeypatch.setenv('SQL_OPTIMIZATION_ITEMS_FIXTURE','items-no-rewrite')
    monkeypatch.setenv('SQL_OPTIMIZATION_MODEL_FIXTURE','normal')
    service=configured_service()
    assert service.items_mode=='qwen' and service.items_fixture=='items-no-rewrite' and service.fixture=='normal'


def test_items_invalid_date_before_model(items_service):
    factory,fetch,measure,context=items_service
    with TestClient(create_app(factory('items-exists'))) as client:
        result=client.post('/optimize',json={'scenario':ORDER_ITEMS_SCENARIO,'date':'2025-02-29'})
    assert result.status_code==400 and result.json()['model_requests']==0
    context.assert_not_called()
    fetch.assert_not_called()
    measure.assert_not_called()
