"""Single-team authenticated review API; touches only dedicated review storage."""
import hmac
import json
import os
from contextlib import contextmanager
import psycopg
from fastapi import APIRouter,HTTPException,Request,Depends
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from sql_optimization_agent.batch_history import _decode,validate,report_id,check_review,HistoryError

router=APIRouter(prefix='/reviews')


def identity(request:Request):
    header=request.headers.get('authorization','')
    if not header.startswith('Bearer '):raise HTTPException(401,'Bearer authentication required')
    try:
        users=json.loads(os.environ.get('REVIEW_TOKENS','{}'))
        for token,user in users.items():
            if hmac.compare_digest(header[7:],token) and token and user.get('role') in ('viewer','reviewer'):
                if not isinstance(user.get('name'),str) or not user['name'].strip() or len(user['name'])>200:break
                return user
    except (ValueError,TypeError,AttributeError):pass
    raise HTTPException(401,'Invalid credentials')


def writer(user=Depends(identity)):
    if user['role']!='reviewer':raise HTTPException(403,'Reviewer permission required')
    return user


@contextmanager
def connection():
    names=('REVIEW_DB_HOST','REVIEW_DB_PORT','REVIEW_DB_NAME','REVIEW_DB_USER','REVIEW_DB_PASSWORD')
    if not all(os.environ.get(n) for n in names):raise HTTPException(503,'Review storage configuration missing')
    if os.environ['REVIEW_DB_USER']!='review_app':raise HTTPException(503,'Dedicated review_app account required')
    if os.environ['REVIEW_DB_NAME']=='sql_optimization':raise HTTPException(503,'Dedicated review database required')
    try:
        with psycopg.connect(host=os.environ['REVIEW_DB_HOST'],port=os.environ['REVIEW_DB_PORT'],
            dbname=os.environ['REVIEW_DB_NAME'],user=os.environ['REVIEW_DB_USER'],password=os.environ['REVIEW_DB_PASSWORD'],
            connect_timeout=5,options='-c statement_timeout=10000 -c lock_timeout=5000') as c:yield c
    except psycopg.Error:raise HTTPException(503,'Review storage operation failed') from None


def fetch(c,batch,entry=None):
    row=c.execute('SELECT raw FROM review_batches WHERE id=%s',(batch,)).fetchone()
    if row is None:raise HTTPException(404,'Batch not found')
    report=_decode(bytes(row[0]))
    if entry is not None and not 1<=entry<=len(report['results']):raise HTTPException(404,'Entry not found')
    return report,bytes(row[0])


@router.post('/batches')
async def import_batch(request:Request,user=Depends(writer)):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>32*1024*1024:raise HTTPException(413,'Report exceeds 32 MiB')
    raw=bytes(data)
    try:report=validate(_decode(raw))
    except HistoryError as e:raise HTTPException(400,str(e)) from None
    batch=report_id(report)
    with connection() as c:
        inserted=c.execute('INSERT INTO review_batches(id,raw) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING id',(batch,raw)).fetchone()
        if inserted:
            with c.cursor() as cursor:
                cursor.executemany('INSERT INTO review_entries(batch,entry) VALUES (%s,%s)',[(batch,r['index']) for r in report['results']])
    return {'batch_id':batch,'existing':inserted is None}


@router.get('/batches')
def batches(user=Depends(identity)):
    with connection() as c:rows=c.execute('SELECT id,created_at FROM review_batches ORDER BY created_at,id').fetchall()
    return [{'batch_id':b,'created_at':t} for b,t in rows]


@router.get('/batches/{batch}')
def batch_detail(batch:str,user=Depends(identity)):
    with connection() as c:report,_=fetch(c,batch)
    return {'batch_id':batch,'report':report}


@router.get('/batches/{batch}/raw')
def raw_report(batch:str,user=Depends(identity)):
    with connection() as c:_,raw=fetch(c,batch)
    return Response(raw,media_type='application/json')


@router.get('/batches/{batch}/entries/{entry}')
def entry_history(batch:str,entry:int,user=Depends(identity)):
    with connection() as c:
        report,_=fetch(c,batch,entry)
        # A single aggregate statement gives one snapshot of version and all events.
        events=c.execute('SELECT version,decision,reviewer,reason,created_at FROM review_events WHERE batch=%s AND entry=%s ORDER BY version',(batch,entry)).fetchall()
    return {'batch_id':batch,'entry':report['results'][entry-1],'version':len(events),
            'reviews':[dict(zip(('version','decision','reviewer','reason','time'),row)) for row in events]}


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version:int=Field(ge=0)
    decision:str
    reason:str=Field(min_length=1,max_length=4000)


@router.post('/batches/{batch}/entries/{entry}/reviews')
def submit(batch:str,entry:int,body:Decision,user=Depends(writer)):
    with connection() as c:
        report,_=fetch(c,batch,entry)
        try:check_review(report,entry,body.decision,user['name'],body.reason)
        except HistoryError as e:raise HTTPException(400,str(e)) from None
        c.execute('SELECT entry FROM review_entries WHERE batch=%s AND entry=%s FOR UPDATE',(batch,entry)).fetchone()
        version=c.execute('SELECT count(*) FROM review_events WHERE batch=%s AND entry=%s',(batch,entry)).fetchone()[0]
        if version!=body.version:raise HTTPException(409,{'message':'Stale review version; reread before submitting','current_version':version})
        row=c.execute('INSERT INTO review_events(batch,entry,version,decision,reviewer,reason) VALUES (%s,%s,%s,%s,%s,%s) RETURNING created_at',
            (batch,entry,version+1,body.decision,user['name'],body.reason)).fetchone()
    return {'version':version+1,'reviewer':user['name'],'decision':body.decision,'time':row[0]}
