from pathlib import Path
from unittest.mock import Mock
import pytest
from sql_optimization_agent.sql_intake import recognize_query,SQLIntakeError
from sql_optimization_agent.date_interval import bounds
SQL=Path('sql/examples/orders_date_interval.sql').read_text()
@pytest.mark.parametrize('start,end',[('2025-06-15','2025-06-15'),('2025-01-31','2025-02-01'),('2025-12-31','2026-01-01'),('2024-02-29','2024-03-01')])
def test_valid(start,end):
    sql=SQL.replace('2025-06-15',start).replace('2025-06-16',end)
    assert recognize_query(sql)==('orders_date_interval',[start,end])
@pytest.mark.parametrize('sql,status',[(SQL.replace('2025-06-15','2025-06-17'),'invalid_date'),(SQL.replace('2025-06-16','9999-12-31'),'invalid_date'),(SQL.replace('2025-06-15','2025-02-29'),'invalid_date'),(SQL.rstrip(';\n')+' AND status IS NOT NULL','unsupported_query_shape'),(SQL+'SELECT 1','unsupported_query_shape')])
def test_reject_before_io(monkeypatch,sql,status):
    import psycopg,httpx
    no=Mock(side_effect=AssertionError('I/O forbidden'))
    monkeypatch.setattr(psycopg,'connect',no);monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no)
    with pytest.raises(SQLIntakeError) as e:recognize_query(sql)
    assert e.value.status==status
    no.assert_not_called()
