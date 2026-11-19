import json
from pathlib import Path
import pytest
from sql_optimization_agent.batch_history import *
SOURCE=Path('reports/batch_sql_diagnosis_acceptance.json')

def test_import_detail_review(tmp_path):
    raw=SOURCE.read_bytes();a=import_report(tmp_path,SOURCE);b=import_report(tmp_path,SOURCE)
    assert a['batch_id']==b['batch_id'] and b['existing'] and len(history(tmp_path))==1
    batch=a['batch_id']
    assert detail(tmp_path,batch,1)['report']['status']=='rejected'
    assert detail(tmp_path,batch,2)['report']['detail']['sql_diff']
    review(tmp_path,batch,2,'defer','tester','check first')
    review(tmp_path,batch,2,'accept','tester','verified')
    assert [r['decision'] for r in detail(tmp_path,batch,2)['reviews']]==['defer','accept']
    assert (tmp_path/batch/'report.json').read_bytes()==raw
    with pytest.raises(HistoryError,match='Cannot accept'):review(tmp_path,batch,1,'accept','tester','no')
    with pytest.raises(HistoryError,match='Entry not found'):detail(tmp_path,batch,99)
    with pytest.raises(HistoryError,match='Batch not found'):detail(tmp_path,'0'*64)

@pytest.mark.parametrize('body',['{','{}','[]','{"status":"completed","status":"completed"}'])
def test_corrupt(tmp_path,body):
    p=tmp_path/'bad.json';p.write_text(body)
    with pytest.raises(HistoryError):import_report(tmp_path/'store',p)

def test_canonical_id_and_tamper_detection(tmp_path):
    a=import_report(tmp_path/'store',SOURCE)
    p=tmp_path/'formatted.json';p.write_text(json.dumps(json.loads(SOURCE.read_text()),sort_keys=True))
    assert import_report(tmp_path/'store',p)['batch_id']==a['batch_id']
    archive=tmp_path/'store'/a['batch_id']/'report.json'
    r=json.loads(archive.read_text());r['extra']='tamper';archive.write_text(json.dumps(r))
    with pytest.raises(HistoryError,match='integrity'):detail(tmp_path/'store',a['batch_id'])

def test_invalid_ids_and_reviews(tmp_path):
    with pytest.raises(HistoryError):load(tmp_path,'../escape')
    batch=import_report(tmp_path,SOURCE)['batch_id']
    with pytest.raises(HistoryError):review(tmp_path,batch,2,'unknown','a','b')
    with pytest.raises(HistoryError):review(tmp_path,batch,2,'defer','','b')
