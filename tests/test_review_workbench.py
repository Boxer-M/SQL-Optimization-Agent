from fastapi.testclient import TestClient
from sql_optimization_agent.api import create_app
from sql_optimization_agent.service import OptimizationService
import json


def test_page_and_assets_are_available_without_database():
    with TestClient(create_app(OptimizationService({}))) as c:
        page=c.get('/workbench')
        assert page.status_code==200
        assert 'SQL 审核工作台' in page.text
        assert "frame-ancestors 'none'" in page.headers['content-security-policy']
        assert page.headers['cache-control']=='no-store'
        script=c.get('/workbench/app.js')
        assert script.status_code==200 and 'javascript' in script.headers['content-type']


def test_identity_is_server_authoritative(monkeypatch):
    monkeypatch.setenv('REVIEW_TOKENS',json.dumps({'test-viewer':{'name':'read-only','role':'viewer'},'test-reviewer':{'name':'alice','role':'reviewer'}}))
    with TestClient(create_app(OptimizationService({}))) as c:
        assert c.get('/reviews/me').status_code==401
        assert c.get('/reviews/me',headers={'Authorization':'Bearer wrong'}).status_code==401
        for token,role in [('test-viewer','viewer'),('test-reviewer','reviewer')]:
            response=c.get('/reviews/me',headers={'Authorization':'Bearer '+token})
            assert response.status_code==200 and response.json()['role']==role
            assert token not in response.text
        assert c.post('/reviews/batches',headers={'Authorization':'Bearer test-viewer'},json={}).status_code==403


def test_workbench_has_no_persistent_token_or_html_injection_sinks():
    from pathlib import Path
    script=Path('src/sql_optimization_agent/web/app.js').read_text()
    for sink in ('localStorage','sessionStorage','document.cookie','innerHTML','eval('):
        assert sink not in script
