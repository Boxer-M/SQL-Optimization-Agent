from pathlib import Path
from unittest.mock import Mock
import pytest
from sql_optimization_agent.batch_sql import run_batch

FILES=['sql/examples/orders_date_2025_06_15.sql','sql/examples/orders_with_items_2025_06_15.sql','sql/examples/orders_date_interval.sql']


def test_mixed_order_and_no_service_on_rejection(tmp_path):
    unsupported=tmp_path/'unsupported.sql';unsupported.write_text('SELECT 1')
    service=Mock();service.optimize.return_value={'status':'completed','recommendation':{'decision':'keep_original'},'sql_diff':'diff'}
    factory=Mock(return_value=service)
    paths=[str(unsupported),FILES[0],str(tmp_path/'missing.sql'),*FILES[1:]]
    report=run_batch(paths,service_factory=factory,fixture='normal')
    assert report['summary']=={'total':5,'completed':3,'rejected':2,'failed':0}
    assert [r['file'] for r in report['results']]==paths
    assert factory.call_count==3
    assert [r['status'] for r in report['results']]==['rejected','completed','rejected','completed','completed']


def test_exception_continues_and_is_sanitized():
    service=Mock();service.optimize.side_effect=[RuntimeError('private password'),{'status':'completed','sql_diff':'ok'}]
    report=run_batch(FILES[:2],service_factory=Mock(return_value=service))
    assert report['summary']=={'total':2,'completed':1,'rejected':0,'failed':1}
    assert 'private password' not in str(report)
    assert report['results'][0]['detail']['model_requests'] is None


def test_rejection_zero_connections_and_model(monkeypatch,tmp_path):
    import psycopg,httpx
    no=Mock(side_effect=AssertionError('External call forbidden'))
    monkeypatch.setattr(psycopg,'connect',no);monkeypatch.setattr(httpx.HTTPTransport,'handle_request',no)
    path=tmp_path/'unsupported.sql';path.write_text('SELECT 1')
    report=run_batch([str(path),str(tmp_path/'missing')],service_factory=no)
    assert report['summary']['rejected']==2
    no.assert_not_called()


def test_cli_prints_full_batch_and_nonzero(monkeypatch,tmp_path,capsys):
    import runpy,sys,json
    path=tmp_path/'bad.sql';path.write_text('SELECT 1')
    service=Mock();service.optimize.return_value={'status':'completed'}
    monkeypatch.setattr('sql_optimization_agent.service.OptimizationService',Mock(return_value=service))
    monkeypatch.setattr(sys,'argv',['optimize_orders.py','--sql-files',str(path),FILES[0]])
    with pytest.raises(SystemExit) as stop:runpy.run_path('scripts/optimize_orders.py',run_name='__main__')
    assert stop.value.code==1
    assert json.loads(capsys.readouterr().out)['summary']['completed']==1


@pytest.mark.parametrize('status',['no_suggestion','no_rewrite','completed'])
def test_keep_original_is_completion(status):
    service=Mock();service.optimize.return_value={'status':status,'recommendation':{'decision':'keep_original'}}
    report=run_batch(FILES[:1],service_factory=Mock(return_value=service))
    assert report['status']=='completed' and report['summary']['completed']==1


def test_simulated_fixture_selected_per_scenario():
    service=Mock();service.optimize.return_value={'status':'completed'}
    factory=Mock(return_value=service)
    run_batch(FILES,service_factory=factory,fixture='normal')
    assert [call.kwargs['fixture'] for call in factory.call_args_list]==['normal','items-exists','normal']
    assert [call.kwargs['use_qwen'] for call in service.optimize.call_args_list]==[False,True,False]
