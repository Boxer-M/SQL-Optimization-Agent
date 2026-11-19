from fastapi.testclient import TestClient
from sql_optimization_agent.api import create_app
from sql_optimization_agent.service import OptimizationService
from sql_optimization_agent.batch_history import check_review,HistoryError,report_id,validate
import json
from pathlib import Path
import pytest

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('REVIEW_TOKENS',json.dumps({'view-token':{'name':'v','role':'viewer'},'write-token':{'name':'alice','role':'reviewer'}}))
    return TestClient(create_app(OptimizationService({})))


def test_auth_before_storage(client):
    assert client.get('/reviews/batches').status_code==401
    assert client.get('/reviews/batches',headers={'Authorization':'Bearer wrong'}).status_code==401
    h={'Authorization':'Bearer view-token'}
    assert client.post('/reviews/batches',headers=h,content=b'{}').status_code==403
    assert client.post('/reviews/batches/x/entries/1/reviews',headers=h,json={'version':0,'decision':'accept','reason':'x'}).status_code==403


def test_reviewer_cannot_spoof_identity(client):
    response=client.post('/reviews/batches/x/entries/1/reviews',headers={'Authorization':'Bearer write-token'},json={'version':0,'decision':'defer','reason':'x','reviewer':'spoof'})
    assert response.status_code==422


def test_bad_import_before_storage(client):
    h={'Authorization':'Bearer write-token'}
    assert client.post('/reviews/batches',headers=h,content=b'{').status_code==400
    assert client.post('/reviews/batches',headers=h,json={}).status_code==400


def test_missing_storage_is_sanitized(client,monkeypatch):
    monkeypatch.delenv('REVIEW_DB_PASSWORD',raising=False)
    assert client.get('/reviews/batches',headers={'Authorization':'Bearer view-token'}).status_code==503


def test_shared_rule_and_id_compatibility():
    report=validate(json.loads(Path('reports/batch_sql_diagnosis_acceptance.json').read_text()))
    assert report_id(report)==report_id(json.loads(json.dumps(report,sort_keys=True)))
    check_review(report,2,'accept','alice','verified')
    with pytest.raises(HistoryError):check_review(report,1,'accept','alice','invalid')
