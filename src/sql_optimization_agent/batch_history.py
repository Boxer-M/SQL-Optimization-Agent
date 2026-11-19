"""Offline immutable batch archives and append-only review events."""
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime,timezone
import uuid

class HistoryError(ValueError):pass


def _decode(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise HistoryError('Duplicate JSON keys')
            result[key]=value
        return result
    try:return json.loads(data,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise HistoryError('Invalid report JSON') from None


def validate(report):
    if not isinstance(report,dict) or report.get('status') not in ('completed','completed_with_errors'):
        raise HistoryError('Invalid batch report status')
    rows=report.get('results');summary=report.get('summary')
    if not isinstance(rows,list) or not isinstance(summary,dict):raise HistoryError('Missing batch results or summary')
    for number,row in enumerate(rows,1):
        if not isinstance(row,dict) or type(row.get('index')) is not int or row['index']!=number:
            raise HistoryError('Entry indices must be sequential starting at 1')
        if row.get('status') not in ('completed','rejected','failed') or not isinstance(row.get('file'),str) or not isinstance(row.get('detail'),dict):
            raise HistoryError('Invalid batch entry')
        if not isinstance(row['detail'].get('status'),str):raise HistoryError('Missing entry detail status')
    expected={'total':len(rows),**{key:sum(row['status']==key for row in rows) for key in ('completed','rejected','failed')}}
    if any(type(summary.get(k)) is not int or summary[k]!=v for k,v in expected.items()):raise HistoryError('Inconsistent batch summary')
    if (report['status']=='completed') != (expected['rejected']==expected['failed']==0):raise HistoryError('Inconsistent batch status')
    return report


def _read(path):
    try:
        with Path(path).open('rb') as f:data=f.read(32*1024*1024+1)
    except OSError:raise HistoryError('Unable to read report or history file') from None
    if len(data)>32*1024*1024:raise HistoryError('Report exceeds 32 MiB budget')
    return data


def import_report(root,source):
    raw=_read(source);report=validate(_decode(raw))
    batch=report_id(report)
    directory=Path(root)/batch;directory.mkdir(parents=True,exist_ok=True)
    path=directory/'report.json'
    try:
        with path.open('xb') as f:f.write(raw)
        existed=False
    except FileExistsError:
        if validate(_decode(_read(path)))!=report:raise HistoryError('Archive conflict')
        existed=True
    return {'batch_id':batch,'existing':existed,'summary':report['summary']}


def load(root,batch):
    if not re.fullmatch('[0-9a-f]{64}',batch):raise HistoryError('Invalid batch ID')
    directory=Path(root)/batch
    if not (directory/'report.json').is_file():raise HistoryError('Batch not found')
    report=validate(_decode(_read(directory/'report.json')))
    expected=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if expected!=batch:raise HistoryError('Archive integrity check failed')
    return directory,report


def history(root):
    path=Path(root)
    if not path.exists():return []
    result=[]
    for directory in sorted(path.iterdir()):
        if directory.is_dir() and re.fullmatch('[0-9a-f]{64}',directory.name):
            _,report=load(root,directory.name)
            result.append({'batch_id':directory.name,'summary':report['summary']})
    return result


def detail(root,batch,index=None):
    directory,report=load(root,batch)
    if index is not None and (index<1 or index>len(report['results'])):raise HistoryError('Entry not found')
    events=[]
    for path in sorted((directory/'reviews').glob('*.json')):
        event=_decode(_read(path))
        if index is None or event['entry']==index:events.append(event)
    events.sort(key=lambda e:(e['time'],e['review_id']))
    return {'batch_id':batch,'report':report if index is None else report['results'][index-1],'reviews':events}


def review(root,batch,index,decision,reviewer,reason):
    if decision not in ('accept','defer','reject'):raise HistoryError('Decision must be accept, defer or reject')
    if not reviewer.strip() or not reason.strip() or len(reviewer)>200 or len(reason)>4000:raise HistoryError('Reviewer and reason are required (limits 200/4000 characters)')
    directory,report=load(root,batch)
    if index<1 or index>len(report['results']):raise HistoryError('Entry not found')
    check_review(report,index,decision,reviewer,reason)
    event={'review_id':uuid.uuid4().hex,'batch_id':batch,'entry':index,'decision':decision,
           'reviewer':reviewer,'reason':reason,'time':datetime.now(timezone.utc).isoformat()}
    reviews=directory/'reviews';reviews.mkdir(exist_ok=True)
    with (reviews/(event['review_id']+'.json')).open('x') as f:json.dump(event,f,ensure_ascii=False,indent=2)
    return event


def check_review(report,index,decision,reviewer,reason):
    if decision not in ('accept','defer','reject'):raise HistoryError('Invalid decision')
    if not reviewer.strip() or not reason.strip() or len(reviewer)>200 or len(reason)>4000:raise HistoryError('Reviewer and reason are required')
    if index<1 or index>len(report['results']):raise HistoryError('Entry not found')
    row=report['results'][index-1];evidence=row['detail']
    from sql_optimization_agent.performance import validation_passed
    if decision=='accept' and not (row['status']=='completed' and evidence.get('status')=='completed'
                                  and isinstance(evidence.get('candidate'),dict) and isinstance(evidence['candidate'].get('sql'),str) and evidence['candidate']['sql'].strip()
                                  and validation_passed(evidence)):
        raise HistoryError('Cannot accept: a verified result and candidate SQL are required')


def report_id(report):
    return hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
