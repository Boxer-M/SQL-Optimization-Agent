"""Offline plan structure and errors, with no backend dependencies in the CLI."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from sql_optimization_agent.tools.plan_diagnosis import PlanInputError,diagnose,load_and_diagnose
from sql_optimization_agent.tools.sql_benchmark import _plan_nodes


def test_tree_scan_join_sort_and_zero_actuals_preserved():
    node={'Node Type':'Sort','Sort Key':['o.order_id'],'Actual Rows':0,'Actual Loops':1,'Plans':[
        {'Node Type':'Nested Loop','Join Type':'Semi','Actual Rows':0,'Actual Loops':1,'Plans':[
            {'Node Type':'Seq Scan','Parallel Aware':True,'Relation Name':'orders','Filter':'(x = 1)',
             'Plan Rows':100,'Actual Rows':0,'Actual Loops':1},
            {'Node Type':'Index Only Scan','Relation Name':'order_items','Index Name':'items_idx',
             'Index Cond':'(order_id = o.order_id)','Actual Rows':0,'Actual Loops':0}]}]}
    plan=diagnose([{'Plan':node}])['plans'][0]
    root=plan['root'];join=root['children'][0];scan=join['children'][0];index=join['children'][1]
    assert join['parent_id']==root['node_id'] and scan['parent_id']==join['node_id']
    assert scan['scan_method']=='Parallel Seq Scan'
    assert join['details']['Join Type']=='Semi'
    assert index['details']['Index Cond']=='(order_id = o.order_id)'
    assert index['measurement']=='actual' and index['details']['Actual Loops']==0
    assert plan['indexes']==['items_idx']
    assert {o['kind'] for o in plan['observations']}=={'sort','sequential_scan'}
    assert all('bottleneck status requires measurement' in o['interpretation'] for o in plan['observations'])
    assert scan['details']==_plan_nodes(node)[2]


def test_estimated_only_is_explicit_and_actuals_not_invented():
    plan=diagnose({'Plan':{'Node Type':'Index Scan','Plan Rows':20,'Plan Width':8,'Total Cost':4}})['plans'][0]
    assert plan['measurement']=='estimated_only'
    assert not plan['runtime_data_present'] and plan['execution_time_ms'] is None
    assert plan['root']['measurement']=='estimated_only'
    assert 'Actual Rows' not in plan['root']['details']


def test_multiple_documents_have_distinct_root_ids():
    result=diagnose([{'Plan':{'Node Type':'Result'}},{'Plan':{'Node Type':'Result'}}])
    assert result['plan_count']==2
    assert [r['root']['node_id'] for r in result['plans']]==['0','1']


@pytest.mark.parametrize('value',[{},[],None,{'Plan':None},[{'unrelated':1}]])
def test_missing_root(value):
    with pytest.raises(PlanInputError,match='[Pp]lan|Plan') as error:diagnose(value)
    assert error.value.code=='missing_plan_root'


@pytest.mark.parametrize('text',['not JSON','{"Plan":','{"Plan":{},"Plan":{}}','{"Plan":{"Node Type":"Result","Plan Rows":NaN}}'])
def test_invalid_json(tmp_path,text):
    path=tmp_path/'invalid.json';path.write_text(text)
    with pytest.raises(PlanInputError) as error:load_and_diagnose(path)
    assert error.value.code=='invalid_json'


@pytest.mark.parametrize('node',[{}, {'Node Type':'Result','Plans':{}},
                               {'Node Type':'Result','Actual Rows':-1}, {'Node Type':'Seq Scan','Schema':{}},
                               {'Node Type':'Seq Scan','Parallel Aware':'true'}])
def test_malformed_nodes_have_structured_errors(node):
    with pytest.raises(PlanInputError) as error:diagnose({'Plan':node})
    assert error.value.code=='invalid_plan_node'


def test_cli_imports_no_database_or_model_libraries(tmp_path):
    path=tmp_path/'estimate.json';path.write_text(json.dumps([{'Plan':{'Node Type':'Seq Scan','Plan Rows':12}}]))
    script=Path(__file__).resolve().parents[1]/'scripts/diagnose_plan.py'
    code='''import runpy,sys,socket
def forbidden(*args,**kwargs): raise AssertionError('No network allowed')
socket.socket.connect=forbidden
sys.argv=[sys.argv[1],sys.argv[2]]
try: runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as e: assert e.code==0
assert not {'psycopg','httpx','dotenv'} & set(sys.modules)
'''
    result=subprocess.run([sys.executable,'-c',code,str(script),str(path)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['plans'][0]['measurement']=='estimated_only'


def test_cli_errors_have_nonzero_exit_without_input_leak(tmp_path):
    path=tmp_path/'bad.json';path.write_text('sensitive-not-json')
    script=Path(__file__).resolve().parents[1]/'scripts/diagnose_plan.py'
    result=subprocess.run([sys.executable,str(script),str(path)],capture_output=True,text=True)
    assert result.returncode==1 and not result.stdout
    assert json.loads(result.stderr)['error_code']=='invalid_json'
    assert 'sensitive-not-json' not in result.stderr
