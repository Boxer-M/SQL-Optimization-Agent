from copy import deepcopy
import pytest
from sql_optimization_agent.sql_diff import attach_sql_diff
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.order_date import ORDER_DATE_SQL
from sql_optimization_agent.qwen_workflow import parse_suggestion,fixture_content
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.order_items_benchmark import JOIN_COUNT,EXISTS_COUNT


def date_report():
    context=OrderDateContext.parse('2025-06-15')
    _,candidate=parse_suggestion(fixture_content('normal',context),context)
    return {'status':'completed','comparison':{'equivalent':True},
            'diagnostic_context':{'original_sql':ORDER_DATE_SQL},'candidate':{'sql':candidate.sql}}


def test_date_stable_preserves_placeholders():
    report=date_report();original=deepcopy(report)
    diff=attach_sql_diff(report)['sql_diff']
    assert '-WHERE created_at::date = %s::date' in diff
    assert '+WHERE created_at >= %s' in diff and '+  AND created_at < %s' in diff
    assert diff.startswith('--- original.sql\n+++ candidate.sql\n')
    assert diff==attach_sql_diff(report)['sql_diff'] and report==original


def test_items():
    r=date_report();r['sql']={'join_count_distinct':JOIN_COUNT};r['candidate']={'sql':EXISTS_COUNT}
    diff=attach_sql_diff(r)['sql_diff']
    assert '-SELECT COUNT(DISTINCT o.order_id)' in diff and '+SELECT COUNT(*)' in diff
    assert 'EXISTS (SELECT 1' in diff and '%s::timestamp' in diff


@pytest.mark.parametrize('candidate,verdict',[([2,4,6],'inconclusive'),([11,12,13],'regressed')])
def test_keep_original_still_has_diff(candidate,verdict):
    report=date_report();report['benchmark']={'execution_ms':{'original':[3,5,7],'rewritten':candidate}}
    r=attach_sql_diff(attach_recommendation(attach_assessment(report)))
    assert r['performance_assessment']['verdict']==verdict
    assert r['recommendation']['decision']=='keep_original' and r['sql_diff']


@pytest.mark.parametrize('status',['no_suggestion','no_rewrite','result_mismatch','invalid_suggestion','validation_error'])
def test_unavailable(status):
    r=date_report();r['status']=status
    assert attach_sql_diff(r)['sql_diff'] is None


def test_missing_candidate_or_failed_validation():
    r=date_report();r.pop('candidate');assert attach_sql_diff(r)['sql_diff'] is None
    r=date_report();r['comparison']['equivalent']=False
    assert attach_sql_diff(r)['sql_diff'] is None


def test_diff_makes_no_external_calls(monkeypatch):
    import psycopg,httpx
    from unittest.mock import Mock
    forbidden=Mock(side_effect=AssertionError('No I/O allowed'))
    monkeypatch.setattr(psycopg,'connect',forbidden)
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',forbidden)
    assert attach_sql_diff(date_report())['sql_diff']
    forbidden.assert_not_called()
