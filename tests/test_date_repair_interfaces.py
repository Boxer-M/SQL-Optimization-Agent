"""Both adapters share the repair gate and real-client POST budget."""

from collections import Counter
from contextlib import nullcontext
import json
from unittest.mock import MagicMock, Mock

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
import httpx
import pytest

from sql_optimization_agent.api import create_app
from sql_optimization_agent.mcp_server import create_server
from sql_optimization_agent.service import configured_service, OptimizationService, ORDERS_DATE_SCENARIO
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.qwen_client import request_suggestion
from sql_optimization_agent.qwen_workflow import fixture_content
from sql_optimization_agent.tools.sql_benchmark import QueryResult

CONTEXT=OrderDateContext.parse('2025-06-15')
CONFIG={'QWEN_API_KEY':'fake-key','QWEN_MODEL':'test','QWEN_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1'}
GOOD=QueryResult([],Counter({(1,):825}),825,1)
BAD=QueryResult([],Counter({(1,):825,(2,):1}),826,1)


@pytest.fixture
def anyio_backend():return 'asyncio'


@pytest.fixture
def factory(monkeypatch):
    from sql_optimization_agent import qwen_workflow as workflow
    monkeypatch.setattr(workflow,'diagnostic_context',lambda *args:{'date_context':CONTEXT.as_dict()})
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=('db','reader','on','repeatable read','test')
    monkeypatch.setattr(workflow,'readonly_connection',lambda _:nullcontext(conn))
    def create(enabled,success):
        fetch=Mock(side_effect=[GOOD,BAD,GOOD,GOOD if success else BAD])
        measure=Mock(return_value={'samples_per_query':7,'execution_ms':{'original':[10]*7,'rewritten':[1]*7}})
        monkeypatch.setattr(workflow,'_fetch',fetch)
        def measured(*args,**kwargs):
            assert fetch.call_count==4
            return {'samples_per_query':7,'execution_ms':{'original':[10]*7,'rewritten':[1]*7}}
        measure.side_effect=measured
        monkeypatch.setattr(workflow,'measure_pair',measure)
        posts=[]
        def handle(request):
            posts.append(json.loads(request.content))
            name='normal' if len(posts)==2 and success else 'wrong-upper'
            return httpx.Response(200,json={'model':'test','choices':[{'finish_reason':'stop',
                'message':{'content':fixture_content(name,CONTEXT)}}]})
        monkeypatch.setattr(workflow,'request_suggestion',lambda ctx,config:request_suggestion(ctx,config,transport=httpx.MockTransport(handle)))
        return OptimizationService(CONFIG,date_repair_enabled=enabled),posts,measure
    return create


def verify(report,posts,measure,enabled,success):
    expected=2 if enabled else 1
    assert len(posts)==report['model_requests']==len(report['attempts'])==expected
    assert report['attempts'][0]['comparison']['original_rows']==825
    assert report['attempts'][0]['comparison']['candidate_rows']==826
    assert report['attempts'][0]['benchmark'] is None
    if enabled and success:
        assert report['comparison']['equivalent'] is True
        assert report['recommendation']['decision']=='use_candidate'
        measure.assert_called_once()
    else:
        assert report['recommendation']['decision']=='keep_original'
        assert all(a['benchmark'] is None for a in report['attempts'])
        measure.assert_not_called()
    if enabled:
        assert 'repair_feedback' in json.loads(posts[1]['messages'][1]['content'])


@pytest.mark.parametrize('enabled,success',[(False,True),(True,True),(True,False)])
def test_http_transport_budget(factory,enabled,success):
    service,posts,measure=factory(enabled,success)
    with TestClient(create_app(service)) as client:
        response=client.post('/optimize',json={'scenario':ORDERS_DATE_SCENARIO})
    assert response.status_code==200
    verify(response.json(),posts,measure,enabled,success)


@pytest.mark.anyio
@pytest.mark.parametrize('enabled,success',[(False,True),(True,True),(True,False)])
async def test_mcp_transport_budget(factory,enabled,success):
    service,posts,measure=factory(enabled,success)
    async with create_connected_server_and_client_session(create_server(service)) as session:
        tools=await session.list_tools()
        assert set(tools.tools[0].inputSchema['properties'])=={'scenario','date'}
        result=await session.call_tool('optimize_orders_date',{'scenario':ORDERS_DATE_SCENARIO})
    verify(result.structuredContent,posts,measure,enabled,success)


@pytest.mark.parametrize('value,expected',[(None,False),('false',False),('true',True),(' TRUE ',True)])
def test_server_setting(monkeypatch,value,expected):
    monkeypatch.delenv('SQL_OPTIMIZATION_DATE_REPAIR',raising=False)
    if value is not None:monkeypatch.setenv('SQL_OPTIMIZATION_DATE_REPAIR',value)
    assert configured_service().date_repair_enabled is expected


def test_invalid_server_setting_fails_without_work(monkeypatch):
    monkeypatch.setenv('SQL_OPTIMIZATION_DATE_REPAIR','typo')
    report=configured_service().optimize(ORDERS_DATE_SCENARIO)
    assert report['status']=='invalid_config' and report['model_requests']==0
    assert report['recommendation']['decision']=='not_evaluated'


def test_http_cannot_enable_repair_in_body():
    with TestClient(create_app(OptimizationService({}))) as client:
        response=client.post('/optimize',json={'scenario':ORDERS_DATE_SCENARIO,'repair_on_mismatch':True})
    assert response.status_code==422


def test_enabled_server_does_not_change_items_scenario(monkeypatch):
    run=Mock(return_value={'status':'completed'})
    monkeypatch.setattr('sql_optimization_agent.service.run_items',run)
    service=OptimizationService({},date_repair_enabled=True)
    report=service.optimize('orders_with_items_count')
    assert report['status']=='completed'
    run.assert_called_once()
