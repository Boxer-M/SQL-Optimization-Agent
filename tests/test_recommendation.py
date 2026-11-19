"""Recommendation rules must not confuse verified execution with adoption."""

from unittest.mock import Mock
import json
from pathlib import Path

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from sql_optimization_agent.api import create_app
from sql_optimization_agent.mcp_server import create_server
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.service import OptimizationService,ORDERS_DATE_SCENARIO


def report(original=(10,11),candidate=(1,2),status='completed'):
    return {'status':status,'comparison':{'equivalent':True},
            'benchmark':{'execution_ms':{'original':list(original),'rewritten':list(candidate)},'samples_per_query':2}}


@pytest.mark.parametrize('original,candidate,decision,reason',[
    ((10,11),(1,2),'use_candidate','validated_and_improved'),
    ((1,2),(10,11),'keep_original','performance_regressed'),
    ((1,10),(2,3),'keep_original','performance_inconclusive'),
    ((3,4),(2,3),'keep_original','performance_inconclusive'),
])
def test_verified_performance_decisions(original,candidate,decision,reason):
    result=attach_recommendation(attach_assessment(report(original,candidate)))
    assert result['status']=='completed'
    assert result['recommendation']=={'decision':decision,'reason_code':reason,'advisory_only':True}


@pytest.mark.parametrize('status,reason',[
    ('result_mismatch','candidate_rejected'),('membership_mismatch','candidate_rejected'),
    ('count_mismatch','candidate_rejected'),('invalid_suggestion','candidate_rejected'),
    ('no_suggestion','model_kept_original'),('no_rewrite','model_kept_original'),
])
def test_rejection_or_retention_overrides_stale_improvement(status,reason):
    r=report(status=status);r['performance_assessment']={'verdict':'improved'}
    result=attach_recommendation(r)
    assert result['status']==status and result['recommendation']['decision']=='keep_original'
    assert result['recommendation']['reason_code']==reason


@pytest.mark.parametrize('status',['invalid_date','unsupported_scenario','missing_config','authentication_error',
                                 'quota_exceeded','service_error','validation_error','model_timeout'])
def test_errors_remain_not_evaluated(status):
    result=attach_recommendation(report(status=status))
    assert result['status']==status
    assert result['recommendation']['decision']=='not_evaluated'
    assert result['recommendation']['reason_code']=='evaluation_error'


def test_missing_performance_or_validation_cannot_recommend():
    r=report();r['benchmark']=None
    assert attach_recommendation(attach_assessment(r))['recommendation']['reason_code']=='performance_not_assessed'
    r=report();r['comparison']['equivalent']=False;r['performance_assessment']={'verdict':'improved'}
    assert attach_recommendation(r)['recommendation']['reason_code']=='validation_not_confirmed'


def test_two_captured_inconclusive_cases_keep_original_despite_lower_median():
    source=json.loads((Path(__file__).resolve().parents[1]/'reports/performance_assessment_reused_live.json').read_text())
    cases=[row for row in source['cases'] if row['performance_assessment']['verdict']=='inconclusive']
    assert len(cases)==2
    for row in cases:
        evidence=row['validation']
        report={'status':row['original_status'],'comparison':evidence.get('comparison'),
                'membership':evidence.get('membership'),'counts':evidence.get('counts'),
                'benchmark':{'execution_ms':row['execution_ms'],'samples_per_query':7}}
        result=attach_recommendation(attach_assessment(report))
        assert result['performance_assessment']['median_difference_ms']<0
        assert result['performance_assessment']['verdict']=='inconclusive'
        assert result['recommendation']['decision']=='keep_original'
        assert result['recommendation']['reason_code']=='performance_inconclusive'


@pytest.fixture
def anyio_backend(): return 'asyncio'


@pytest.mark.anyio
async def test_three_entries_share_recommendation(monkeypatch):
    monkeypatch.setattr('sql_optimization_agent.service.run_workflow',Mock(side_effect=lambda *a,**kw:report()))
    service=OptimizationService({},fixture='normal')
    direct=service.optimize(ORDERS_DATE_SCENARIO)
    with TestClient(create_app(service)) as client:
        http=client.post('/optimize',json={'scenario':ORDERS_DATE_SCENARIO}).json()
    async with create_connected_server_and_client_session(create_server(service)) as session:
        mcp=await session.call_tool('optimize_orders_date',{'scenario':ORDERS_DATE_SCENARIO})
    assert direct['recommendation']==http['recommendation']==mcp.structuredContent['recommendation']
    assert direct['status']==http['status']==mcp.structuredContent['status']=='completed'
