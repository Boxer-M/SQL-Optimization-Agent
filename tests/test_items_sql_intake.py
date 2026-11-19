import json
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock
import pytest
from sql_optimization_agent.sql_intake import recognize_query, SQLIntakeError

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/'sql/examples/orders_with_items_2025_06_15.sql').read_text()
CASES=[(SQL.replace('COUNT(DISTINCT o.order_id)','COUNT(*)'),'unsupported_query_shape'),
       (SQL.replace('i.order_id = o.order_id','i.order_id = o.customer_id'),'unsupported_query_shape'),
       (SQL.rstrip(';\n')+" AND o.status = 'paid'",'unsupported_query_shape'),
       (SQL.replace('2025-06-15','2025-02-29'),'invalid_date'),
       (SQL.replace('2025-06-16','2025-06-17'),'invalid_date'),
       (SQL.replace('2025-06-15 00:00:00','2025-06-15 00:00:01'),'invalid_date'),
       (SQL.replace('2025-06-16 00:00:00','2025-06-16 00:00:01'),'invalid_date'),
       (SQL+'SELECT 1','unsupported_query_shape')]

@pytest.mark.parametrize('sql,status',CASES)
def test_rejected_before_service(monkeypatch,tmp_path,capsys,sql,status):
    import psycopg
    path=tmp_path/'input.sql';path.write_text(sql)
    db=Mock(side_effect=AssertionError('DB forbidden'));model=Mock(side_effect=AssertionError('Model forbidden'))
    service=Mock(side_effect=AssertionError('Service forbidden'))
    monkeypatch.setattr(psycopg,'connect',db)
    monkeypatch.setattr(psycopg.Connection,'connect',db)
    monkeypatch.setattr('sql_optimization_agent.qwen_workflow.request_suggestion',model)
    monkeypatch.setattr('sql_optimization_agent.order_items_qwen.request_suggestion',model)
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',service)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-file',str(path)])
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==1
    assert json.loads(capsys.readouterr().out)['status']==status
    db.assert_not_called();model.assert_not_called();service.assert_not_called()

@pytest.mark.parametrize('day',['2025-06-15','2025-06-16'])
def test_dispatch(monkeypatch,capsys,day):
    path=ROOT/'sql/examples'/('orders_with_items_'+day.replace('-','_')+'.sql')
    assert recognize_query(path.read_text())==('orders_with_items_count',day)
    service=Mock();service.optimize.return_value={'status':'completed'}
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',lambda **kwargs:service)
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-file',str(path),'--qwen','--fixture','items-exists'])
    with pytest.raises(SystemExit) as stopped:runpy.run_path(str(ROOT/'scripts/optimize_orders.py'),run_name='__main__')
    assert stopped.value.code==0
    service.optimize.assert_called_once_with('orders_with_items_count',date=day,use_qwen=True)
    assert json.loads(capsys.readouterr().out)['sql_intake']['recognized_scenario']=='orders_with_items_count'
