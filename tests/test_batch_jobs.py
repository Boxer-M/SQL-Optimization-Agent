import json
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sql_optimization_agent.api import create_app
from sql_optimization_agent.service import OptimizationService
from sql_optimization_agent.batch_jobs import validate_files,settings


def test_files_limits():
    assert validate_files([{'name':'a.sql','content':'SELECT 1'}])
    for files in [[],[{'name':'a.sql','content':''}]*11,[{'name':'../a.sql','content':''}],[{'name':'a.json','content':''}],[{'name':'a.sql','content':'\ud800'}],[{'name':'a.sql','content':'x'*65537}],[{'name':str(i)+'.sql','content':'x'*60000} for i in range(5)]]:
        with pytest.raises(HTTPException):validate_files(files)


def test_auth_and_configuration_before_queue(monkeypatch):
    monkeypatch.setenv('REVIEW_TOKENS',json.dumps({'viewer':{'name':'v','role':'viewer'},'reviewer':{'name':'r','role':'reviewer'}}))
    with TestClient(create_app(OptimizationService({}))) as c:
        assert c.post('/reviews/jobs',json={'files':[]}).status_code==401
        assert c.post('/reviews/jobs',headers={'Authorization':'Bearer viewer'},json={'files':[]}).status_code==403
        assert c.post('/reviews/jobs/x/resubmit',headers={'Authorization':'Bearer viewer'}).status_code==403
        for body in [{'files':[]},{'files':[{'name':'x.sql','content':'x'}],'mode':'live'}]:
            assert c.post('/reviews/jobs',headers={'Authorization':'Bearer reviewer'},json=body).status_code==400


def test_settings_server_only(monkeypatch):
    monkeypatch.delenv('BATCH_JOB_MODEL_MODE',raising=False)
    assert settings()['mode']=='simulated' and settings()['max_model_requests']==0
    monkeypatch.setenv('BATCH_JOB_MODEL_MODE','invalid')
    with pytest.raises(HTTPException):settings()


def test_content_reader_reuses_reject_before_service():
    from sql_optimization_agent.batch_sql import run_batch
    from sql_optimization_agent.sql_intake import recognize_query
    factory=Mock(side_effect=AssertionError('Service forbidden'))
    report=run_batch(['0'],service_factory=factory,query_reader=lambda _:recognize_query('SELECT 1'))
    assert report['summary']['rejected']==1
    factory.assert_not_called()


def test_worker_interruption_marks_status_and_unlocks(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from sql_optimization_agent import batch_jobs as jobs
    c=MagicMock();c.transaction.side_effect=lambda:nullcontext()
    def execute(sql,params=None):
        result=MagicMock()
        if sql.startswith('SELECT id,files,settings'):result.fetchone.return_value=('job',[{'name':'a.sql','content':'SELECT 1'}],{'mode':'simulated'})
        else:result.fetchone.return_value=(True,)
        return result
    c.execute.side_effect=execute
    monkeypatch.setattr(jobs,'connection',lambda:nullcontext(c));monkeypatch.setattr(jobs,'recover',lambda c:None)
    def interrupted(job):raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):jobs.work_once(on_claim=interrupted)
    assert any('UPDATE review_jobs SET status=%s' in call.args[0] and call.args[1][0]=='interrupted' for call in c.execute.call_args_list)
    assert any('pg_advisory_unlock' in call.args[0] for call in c.execute.call_args_list)


def test_worker_failure_is_sanitized(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from sql_optimization_agent import batch_jobs as jobs
    c=MagicMock();c.transaction.side_effect=lambda:nullcontext()
    def execute(sql,params=None):
        result=MagicMock();result.fetchone.return_value=('job',[{'name':'a.sql','content':'SELECT 1'}],{'mode':'simulated'}) if sql.startswith('SELECT id,files,settings') else (True,)
        return result
    c.execute.side_effect=execute
    monkeypatch.setattr(jobs,'connection',lambda:nullcontext(c));monkeypatch.setattr(jobs,'recover',lambda c:None)
    def fail(job):raise RuntimeError('sensitive credential')
    assert jobs.work_once(on_claim=fail)=='job'
    updates=[call.args[1] for call in c.execute.call_args_list if 'UPDATE review_jobs SET status=%s' in call.args[0]]
    assert updates[0][0]=='failed' and 'sensitive' not in str(updates)


def test_empty_queue_does_not_execute(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from sql_optimization_agent import batch_jobs as jobs
    c=MagicMock();c.transaction.side_effect=lambda:nullcontext();c.execute.return_value.fetchone.return_value=None
    monkeypatch.setattr(jobs,'connection',lambda:nullcontext(c));monkeypatch.setattr(jobs,'recover',lambda c:None)
    assert jobs.work_once() is None
    assert all('pg_try_advisory_lock' not in call.args[0] for call in c.execute.call_args_list)
