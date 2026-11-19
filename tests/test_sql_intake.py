"""Full-AST recognition and CLI rejection before any service work."""

import json
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock

import pytest

from sql_optimization_agent.sql_intake import SQLIntakeError, recognize_date_query, read_date_query
from sql_optimization_agent.tools.sql_benchmark import ORIGINAL

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('day',['2025-06-15','2025-06-16','2024-02-29','2025-12-31'])
def test_only_date_changes(day):
    assert recognize_date_query(ORIGINAL.replace('2025-06-15',day))==day


def test_formatting_and_equivalent_cast_syntax():
    sql="""-- local comment
SELECT customer_id, created_at, status, amount FROM public.orders
WHERE CAST(created_at AS DATE) = CAST('2025-06-16' AS DATE);"""
    assert recognize_date_query(sql)=='2025-06-16'


UNSUPPORTED=[
    ORIGINAL+'; SELECT 1',
    'DELETE FROM public.orders',
    ORIGINAL+" AND status = 'paid'",
    ORIGINAL.replace('customer_id, created_at, status, amount','order_id, created_at, status, amount'),
    ORIGINAL.replace('SELECT','SELECT DISTINCT',1),
    ORIGINAL+' ORDER BY created_at',ORIGINAL+' LIMIT 1',
    ORIGINAL.replace('public.orders','public.other_orders'),
    ORIGINAL.replace('public.orders','public.orders AS o'),
    ORIGINAL.replace('public.orders','orders'),
    ORIGINAL.replace(' = DATE',' <= DATE'),
    ORIGINAL.replace("DATE '2025-06-15'","'2025-06-15'"),
    'WITH extra AS (SELECT 1) '+ORIGINAL,
    ORIGINAL+' UNION ALL '+ORIGINAL,
    ORIGINAL.replace('public.orders','public.orders JOIN public.order_items USING (order_id)'),
]


@pytest.mark.parametrize('sql',UNSUPPORTED)
def test_unsupported_whole_tree(sql):
    with pytest.raises(SQLIntakeError) as caught:recognize_date_query(sql)
    assert caught.value.status=='unsupported_query_shape'


@pytest.mark.parametrize('day',['2025-02-29','2025-13-01','2025-6-15','9999-12-31','private-not-a-date'])
def test_invalid_dates(day):
    with pytest.raises(SQLIntakeError) as caught:recognize_date_query(ORIGINAL.replace('2025-06-15',day))
    assert caught.value.status=='invalid_date'
    assert day not in str(caught.value)


@pytest.mark.parametrize('sql',["SELECT FROM",ORIGINAL.replace("'2025-06-15'","'unterminated"),'SELECT (','','-- only comment'])
def test_syntax_errors_do_not_echo_raw_input(sql):
    with pytest.raises(SQLIntakeError) as caught:recognize_date_query(sql)
    assert caught.value.status=='invalid_sql'
    if sql:
        assert sql not in str(caught.value)


@pytest.mark.parametrize('sql,status',[(UNSUPPORTED[0],'unsupported_query_shape'),
    (UNSUPPORTED[1],'unsupported_query_shape'),(UNSUPPORTED[2],'unsupported_query_shape'),
    (UNSUPPORTED[3],'unsupported_query_shape'),
    (ORIGINAL.replace('2025-06-15','2025-02-29'),'invalid_date'),('SELECT (','invalid_sql')])
def test_cli_rejections_have_zero_database_and_model_calls(monkeypatch,tmp_path,capsys,sql,status):
    import psycopg
    path=tmp_path/'input.sql';path.write_text(sql)
    db=Mock(side_effect=AssertionError('Database forbidden'))
    model=Mock(side_effect=AssertionError('Model forbidden'))
    service=Mock(side_effect=AssertionError('Service must not be constructed'))
    monkeypatch.setattr(psycopg,'connect',db)
    monkeypatch.setattr(psycopg.Connection,'connect',db)
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.request_suggestion',model)
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',service)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-file',str(path)])
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==1
    report=json.loads(capsys.readouterr().out)
    assert report['status']==status and report['model_requests']==0
    db.assert_not_called();model.assert_not_called();service.assert_not_called()


@pytest.mark.parametrize('extra',[['--date','2025-06-15'],['--scenario','orders_date_filter_2025_06_15']])
def test_explicit_arguments_conflict_before_reading(monkeypatch,capsys,extra):
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-file','missing.sql',*extra])
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==2
    assert 'conflicts' in capsys.readouterr().err


@pytest.mark.parametrize('day',['2025-06-15','2025-06-16'])
def test_file_cli_dispatches_only_recognized_date(monkeypatch,tmp_path,capsys,day):
    path=tmp_path/'input.sql';path.write_text(ORIGINAL.replace('2025-06-15',day))
    service=Mock();service.optimize.return_value={'status':'completed'}
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',lambda **kwargs:service)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-file',str(path),'--fixture','normal'])
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==0
    service.optimize.assert_called_once_with('orders_date_filter_2025_06_15',date=day,use_qwen=False)
    report=json.loads(capsys.readouterr().out)
    assert report['sql_intake']['extracted_date']==day
    assert report['sql_intake']['execution']=='repository_parameterized_template'


def test_file_errors_and_size_budget(tmp_path):
    with pytest.raises(SQLIntakeError) as caught:read_date_query(tmp_path/'absent.sql')
    assert caught.value.status=='sql_file_error'
    path=tmp_path/'input.sql';path.write_bytes(b'X'*65537)
    with pytest.raises(SQLIntakeError) as caught:read_date_query(path)
    assert caught.value.status=='sql_input_too_large'
    path.write_bytes(b'\xff')
    with pytest.raises(SQLIntakeError) as caught:read_date_query(path)
    assert caught.value.status=='invalid_sql'


def test_repository_parameterized_template_matches_benchmark_structure():
    from sql_optimization_agent.order_date import ORDER_DATE_SQL
    assert recognize_date_query(ORDER_DATE_SQL.replace('%s', "'2025-06-15'"))=='2025-06-15'
