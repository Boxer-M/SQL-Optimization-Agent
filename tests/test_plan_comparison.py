"""Pure-file comparison regression and real saved-plan acceptance."""

import json
from pathlib import Path
import subprocess
import sys

from sql_optimization_agent.tools.plan_comparison import compare_diagnoses, load_and_compare
from sql_optimization_agent.tools.plan_diagnosis import diagnose, load_and_diagnose

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / 'reports/explain_plans'
SCRIPT = ROOT / 'scripts/diagnose_plan.py'


def test_real_date_plans():
    result = load_and_compare(PLANS/'date_original.json', PLANS/'date_candidate.json')
    scans = {item['feature']['method']: item for item in result['feature_comparison']['scans']}
    assert scans['Parallel Seq Scan']['count_delta'] == -1
    assert scans['Index Scan']['count_delta'] == 1
    assert scans['Index Scan']['feature']['index'] == 'orders_created_at_idx'
    assert scans['Parallel Seq Scan']['original_occurrences'][0]['node_path'] == '0.0'
    assert scans['Index Scan']['candidate_occurrences'][0]['node_path'] == '0'


def test_real_items_plans():
    result = load_and_compare(PLANS/'items_original.json', PLANS/'items_candidate.json')
    features = result['feature_comparison']
    assert {(item['feature']['join_type'],item['count_delta']) for item in features['joins']} == {('Inner',-1),('Semi',1)}
    assert len(features['sorts']) == 1
    assert features['sorts'][0]['count_delta'] == -1
    assert features['sorts'][0]['original_occurrences'][0]['actual_rows'] == 1103


def test_same_plan_has_unchanged_counts():
    result = load_and_compare(PLANS/'items_original.json',PLANS/'items_original.json')
    assert all(item['change'] == 'unchanged_count' for entries in result['feature_comparison'].values() for item in entries)
    assert result['original'] == result['candidate']


def test_estimate_and_actual_are_not_conflated():
    result = load_and_compare(PLANS/'date_original_estimated.json',PLANS/'date_original.json')
    scan = result['feature_comparison']['scans'][0]
    before,after = scan['original_occurrences'][0],scan['candidate_occurrences'][0]
    assert before['measurement'] == 'estimated_only'
    assert before['actual_rows'] is None and before['actual_loops'] is None
    assert before['missing_runtime_fields'] == ['Actual Rows','Actual Loops']
    assert after['measurement'] == 'actual'
    assert after['actual_rows'] == 412 and after['actual_loops'] == 2
    assert after['missing_runtime_fields'] == []


def test_reordered_repeated_nodes_use_counts_not_positions():
    scan = {'Node Type':'Seq Scan','Relation Name':'orders','Actual Rows':0,'Actual Loops':0}
    sort = {'Node Type':'Sort','Sort Key':['order_id']}
    left = diagnose([{'Plan':{'Node Type':'Append','Plans':[scan,sort,scan]}}])
    right = diagnose([{'Plan':{'Node Type':'Append','Plans':[sort,scan]}}])
    result = compare_diagnoses(left,right)['feature_comparison']
    entry = result['scans'][0]
    assert (entry['original_count'],entry['candidate_count'],entry['count_delta']) == (2,1,-1)
    assert [n['node_path'] for n in entry['original_occurrences']] == ['0.0','0.2']
    assert entry['candidate_occurrences'][0]['node_path'] == '0.1'
    assert entry['candidate_occurrences'][0]['actual_rows'] == 0
    assert result['sorts'][0]['change'] == 'unchanged_count'


def test_cli_bad_second_file(tmp_path):
    bad = tmp_path/'bad.json';bad.write_text('{')
    process = subprocess.run([sys.executable,str(SCRIPT),str(PLANS/'date_original.json'),str(bad)],capture_output=True,text=True)
    assert process.returncode == 1 and not process.stdout
    error = json.loads(process.stderr)
    assert error['error_code'] == 'invalid_json'
    assert error['message'].startswith('candidate:')


def test_single_file_cli_unchanged():
    path = PLANS/'date_original.json'
    process = subprocess.run([sys.executable,str(SCRIPT),str(path)],capture_output=True,text=True,check=True)
    assert json.loads(process.stdout) == load_and_diagnose(path)


def test_comparison_cli_is_offline():
    code = '''
import sys,runpy,socket
from unittest.mock import patch
with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')) as connect:
    sys.argv=sys.argv[1:]
    try:runpy.run_path(sys.argv[0],run_name='__main__')
    except SystemExit as error:assert error.code==0
    assert connect.call_count==0
assert not {'psycopg','httpx','dotenv'} & set(sys.modules)
'''
    process = subprocess.run([sys.executable,'-c',code,str(SCRIPT),str(PLANS/'items_original.json'),str(PLANS/'items_candidate.json')],capture_output=True,text=True,check=True)
    assert json.loads(process.stdout)['mode'] == 'comparison'
