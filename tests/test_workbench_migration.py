from contextlib import nullcontext
from pathlib import Path
import runpy
from unittest.mock import MagicMock
import pytest

@pytest.mark.parametrize('existing',[False,True])
def test_migration_preserves_old_data_and_credentials(monkeypatch,existing):
    import psycopg
    statements=[];connections=[]
    def connect(**kwargs):
        connections.append(kwargs);c=MagicMock()
        def execute(query,*args):
            text=str(query);statements.append(text);r=MagicMock()
            if 'FROM pg_roles' in text:r.fetchone.return_value=(False,False,False) if existing else None
            elif 'to_regclass' in text:r.fetchone.return_value=('existing' if existing else None,)
            return r
        c.execute.side_effect=execute
        return nullcontext(c)
    monkeypatch.setattr(psycopg,'connect',connect)
    for key,value in {'REVIEW_DB_HOST':'review-db','REVIEW_DB_PORT':'5432','REVIEW_ADMIN_PASSWORD':'fake-admin','REVIEW_DB_PASSWORD':'fake-app'}.items():monkeypatch.setenv(key,value)
    functions=runpy.run_path(str(Path('scripts/migrate_workbench.py')))
    assert functions['main']()==0
    assert all(c['dbname']=='review_store' for c in connections)
    assert [c['user'] for c in connections]==['postgres','review_app']
    assert any('CREATE ROLE' in s for s in statements)==(not existing)
    assert not any('DROP ' in s or 'TRUNCATE ' in s or 'ALTER ROLE' in s for s in statements)
    if existing:assert not any('CREATE TABLE' in s for s in statements)


def test_unsafe_existing_role_fails_closed(monkeypatch):
    import psycopg
    c=MagicMock();c.execute.return_value.fetchone.return_value=(True,False,False)
    monkeypatch.setattr(psycopg,'connect',lambda **kwargs:nullcontext(c))
    for key in ('REVIEW_DB_HOST','REVIEW_DB_PORT','REVIEW_ADMIN_PASSWORD','REVIEW_DB_PASSWORD'):monkeypatch.setenv(key,'fake')
    functions=runpy.run_path('scripts/migrate_workbench.py')
    assert functions['main']()==1


def test_container_optimization_host_and_readonly_connection(monkeypatch):
    import psycopg
    from sql_optimization_agent.db import readonly_connection
    seen=[];c=MagicMock();c.__enter__.return_value=c
    def connect(**kwargs):seen.append(kwargs);return c
    monkeypatch.setattr(psycopg,'connect',connect)
    with readonly_connection({'POSTGRES_HOST':'postgres','POSTGRES_PORT':'5432','POSTGRES_READER_USER':'sql_optimization_reader','POSTGRES_READER_PASSWORD':'fake-only'}):pass
    assert seen[0]['host']=='postgres' and seen[0]['dbname']=='sql_optimization'
    assert seen[0]['options']=='-c default_transaction_read_only=on' and c.read_only is True
