"""Offline CI tests: real adapters/protocol, controlled model and database boundaries."""

from collections import Counter
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock, Mock

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from sql_optimization_agent.api import create_app
from sql_optimization_agent.mcp_server import create_server
from sql_optimization_agent.service import ORDERS_DATE_SCENARIO, OptimizationService
from sql_optimization_agent.tools.sql_benchmark import QueryResult


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def offline(monkeypatch):
    import sql_optimization_agent.qwen_workflow as workflow

    context = Mock(return_value={"scenario": ORDERS_DATE_SCENARIO})
    monkeypatch.setattr(workflow, 'diagnostic_context', context)
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (
        'sql_optimization', 'sql_optimization_reader', 'on', 'repeatable read', 'PostgreSQL 16 controlled')
    monkeypatch.setattr(workflow, 'readonly_connection', lambda config: nullcontext(connection))
    base = (10001, datetime(2025, 6, 15), 'paid', Decimal('123.45'))
    extra = (10003, datetime(2025, 6, 16), 'pending', Decimal('345.67'))
    def fetch(connection, sql, *args):
        rows = [base, base] + ([extra] if '<=' in sql else [])
        return QueryResult([('customer_id', 23), ('created_at', 1114), ('status', 25), ('amount', 1700)],
                           Counter(rows), len(rows), 100)
    monkeypatch.setattr(workflow, '_fetch', fetch)
    measure = Mock(return_value={'median_ms': {'original': 2.0, 'rewritten': 1.0},
                                 'execution_ms': {'original': [2.0] * 7, 'rewritten': [1.0] * 7}})
    monkeypatch.setattr(workflow, 'measure_pair', measure)
    # Any accidental real model call makes the test fail rather than needing credentials.
    monkeypatch.setattr(workflow, 'request_suggestion', Mock(side_effect=AssertionError('CI must not call Qwen')))
    return lambda fixture='normal': OptimizationService({}, fixture=fixture), measure, context


@pytest.mark.parametrize('fixture,status', [('normal', 'completed'), ('wrong-upper', 'result_mismatch'),
                                           ('malformed', 'invalid_suggestion')])
def test_http_workflow_paths(offline, fixture, status):
    factory, measure, _ = offline
    with TestClient(create_app(factory(fixture))) as client:
        response = client.post('/optimize', json={'scenario': ORDERS_DATE_SCENARIO})
    assert response.status_code == 200
    report = response.json()
    assert report['status'] == status
    assert report['source'] == 'controlled_model_response'
    assert report['stage'] == {'normal': 'complete', 'wrong-upper': 'result_validation',
                               'malformed': 'suggestion_validation'}[fixture]
    assert measure.call_count == (1 if fixture == 'normal' else 0)
    if fixture == 'wrong-upper':
        assert report['benchmark'] is None
        assert report['comparison']['extra_in_candidate'][0]['row'][3] == {'decimal': '345.67'}


def test_http_rejects_unknown_scenario_and_external_sql(offline):
    factory, measure, context = offline
    with TestClient(create_app(factory())) as client:
        response = client.post('/optimize', json={'scenario': 'unknown'})
        assert response.status_code == 400
        assert response.json()['status'] == 'unsupported_scenario'
        assert client.post('/optimize', json={'scenario': ORDERS_DATE_SCENARIO, 'sql': 'SELECT 1'}).status_code == 422
        assert client.post('/optimize', json={'scenario': ORDERS_DATE_SCENARIO, 'date': '2025-06-16'}).status_code == 422
        assert client.post('/optimize', json={}).status_code == 422
    context.assert_not_called()
    measure.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('fixture,status', [('normal', 'completed'), ('wrong-upper', 'result_mismatch')])
async def test_mcp_discovery_and_call_over_protocol(offline, fixture, status):
    factory, measure, _ = offline
    server = create_server(factory(fixture))
    async with create_connected_server_and_client_session(server) as session:
        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == ['optimize_orders_date']
        assert set(tools.tools[0].inputSchema['properties']) == {'scenario'}
        result = await session.call_tool('optimize_orders_date', {'scenario': ORDERS_DATE_SCENARIO})
        assert result.structuredContent['status'] == status
        assert result.isError == (fixture != 'normal')
    assert measure.call_count == (1 if fixture == 'normal' else 0)


@pytest.mark.anyio
async def test_mcp_unknown_scenario_does_not_run_workflow(offline):
    factory, measure, context = offline
    async with create_connected_server_and_client_session(create_server(factory())) as session:
        result = await session.call_tool('optimize_orders_date', {'scenario': 'unknown'})
        assert result.isError
        assert result.structuredContent['status'] == 'unsupported_scenario'
    context.assert_not_called()
    measure.assert_not_called()


def test_cli_uses_shared_service(offline, monkeypatch, capsys):
    factory, measure, _ = offline
    import sql_optimization_agent.service as service
    monkeypatch.setattr(service, 'OptimizationService', lambda *, fixture: factory(fixture))
    monkeypatch.setattr(sys, 'argv', ['optimize_orders.py', '--fixture', 'normal'])
    script = Path(__file__).resolve().parents[1] / 'scripts/optimize_orders.py'
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(script), run_name='__main__')
    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'
    measure.assert_called_once()


@pytest.mark.anyio
async def test_stdio_entrypoint_discovery_and_unknown_scenario():
    parameters = StdioServerParameters(command=sys.executable,
                                      args=['-m', 'sql_optimization_agent.mcp_server'])
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            assert [t.name for t in (await session.list_tools()).tools] == ['optimize_orders_date']
            result = await session.call_tool('optimize_orders_date', {'scenario': 'unsupported'})
            assert result.isError
            assert result.structuredContent['status'] == 'unsupported_scenario'
