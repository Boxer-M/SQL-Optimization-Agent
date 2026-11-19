"""Strict interval boundaries and correctness/timing gates for all adapters."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from sql_optimization_agent.api import create_app
from sql_optimization_agent.mcp_server import create_server
from sql_optimization_agent.performance import assess_samples,attach_assessment
from sql_optimization_agent.service import OptimizationService,ORDERS_DATE_SCENARIO


@pytest.mark.parametrize('original,candidate,verdict,delta,percent',[
    ([10,11,12],[1,2,3],'improved',-9,-900/11),
    ([1,2,3],[10,11,12],'regressed',9,450),
    ([1,4,10],[2,3,5],'inconclusive',-1,-25),
    ([3,4],[2,3],'inconclusive',-1,-100/3.5),
    ([1,2],[2,3],'inconclusive',1,100/1.5),
    ([0,0],[1,1],'regressed',1,None),
    ([0,0],[0,0],'inconclusive',0,None),
])
def test_fixed_samples(original,candidate,verdict,delta,percent):
    assessment=assess_samples(original,candidate)
    assert assessment['verdict']==verdict
    assert assessment['median_difference_ms']==delta
    assert assessment['median_change_percent']==pytest.approx(percent) if percent is not None else assessment['median_change_percent'] is None


def verified():
    return {'status':'completed','stage':'complete','comparison':{'equivalent':True},
            'benchmark':{'execution_ms':{'original':[10,11],'rewritten':[1,2]},'samples_per_query':2}}


@pytest.mark.parametrize('status',['result_mismatch','membership_mismatch','count_mismatch','no_suggestion','no_rewrite','authentication_error'])
def test_rejected_or_kept_original_skips_even_with_stale_samples(status):
    report=verified();report['status']=status
    result=attach_assessment(report)
    assert result['status']==status and result['performance_assessment'] is None


@pytest.mark.parametrize('samples',[[],[float('nan')],[-1],[True]])
def test_incomplete_or_bad_timing_is_not_assessed(samples):
    report=verified();report['benchmark']['execution_ms']['rewritten']=samples
    assert attach_assessment(report)['performance_assessment'] is None


def test_validation_evidence_required_and_status_preserved():
    report=verified();report['comparison']['equivalent']=False
    assert attach_assessment(report)['performance_assessment'] is None
    report=verified();report['benchmark']['samples_per_query']=7
    assert attach_assessment(report)['performance_assessment'] is None
    report=verified();report['membership']={'comparison':{'equivalent':True,'original_rows':2,'candidate_rows':2},
        'join_minus_candidate_count':0,'candidate_minus_join_count':0}
    report['counts']={'join_count_distinct':2,'candidate_count':3}
    assert attach_assessment(report)['performance_assessment'] is None


@pytest.fixture
def anyio_backend(): return 'asyncio'


@pytest.mark.anyio
async def test_public_service_http_mcp_share_the_same_field(monkeypatch):
    monkeypatch.setattr('sql_optimization_agent.service.run_workflow',Mock(side_effect=lambda *a,**kw:verified()))
    service=OptimizationService({},fixture='normal')
    direct=service.optimize(ORDERS_DATE_SCENARIO)
    with TestClient(create_app(service)) as client:
        http=client.post('/optimize',json={'scenario':ORDERS_DATE_SCENARIO}).json()
    async with create_connected_server_and_client_session(create_server(service)) as session:
        mcp=await session.call_tool('optimize_orders_date',{'scenario':ORDERS_DATE_SCENARIO})
    assert direct['performance_assessment']==http['performance_assessment']==mcp.structuredContent['performance_assessment']
    assert direct['status']==http['status']==mcp.structuredContent['status']=='completed'
