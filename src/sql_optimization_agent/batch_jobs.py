"""Durable single-team SQL jobs; HTTP only enqueues, separate workers execute."""
import json,os,uuid
from fastapi import APIRouter,Depends,HTTPException,Request
from psycopg.types.json import Jsonb
from sql_optimization_agent.shared_review import connection,identity,writer
from sql_optimization_agent.batch_history import validate,report_id

router=APIRouter(prefix='/reviews/jobs')


def validate_files(files):
    if not isinstance(files,list) or not 1<=len(files)<=10:raise HTTPException(400,'Choose 1–10 SQL files')
    total=0
    for f in files:
        if not isinstance(f,dict) or set(f)!= {'name','content'} or not isinstance(f['name'],str) or not isinstance(f['content'],str):raise HTTPException(400,'Expected file name and content strings')
        if not f['name'].lower().endswith('.sql') or len(f['name'])>200 or not f['name'] or any(c in f['name'] for c in '/\\\x00'):
            raise HTTPException(400,'SQL file names must be plain .sql names')
        try:size=len(f['content'].encode('utf-8'))
        except UnicodeError:raise HTTPException(400,'SQL content must be valid Unicode') from None
        total+=size
        if size>65536:raise HTTPException(413,'Each SQL file is limited to 64 KiB')
    if total>262144:raise HTTPException(413,'SQL contents exceed 256 KiB total')
    return files


def settings():
    mode=os.environ.get('BATCH_JOB_MODEL_MODE','simulated')
    if mode not in ('simulated','live'):raise HTTPException(503,'Invalid BATCH_JOB_MODEL_MODE')
    return {'mode':mode,'max_files':10,'max_total_bytes':262144,'rows_per_query':10000,'bytes_per_query':16777216,
            'statement_timeout_ms':5000,'samples_per_query':7,'repair_on_mismatch':False,'max_model_requests':10 if mode=='live' else 0}


def enqueue(c,files,user,config,parent=None):
    job=uuid.uuid4().hex
    c.execute('INSERT INTO review_jobs(id,files,settings,status,submitted_by,parent_id) VALUES (%s,%s,%s,\'queued\',%s,%s)',(job,Jsonb(files),Jsonb(config),user['name'],parent))
    return {'job_id':job,'status':'queued'}


@router.post('',status_code=202)
async def submit_job(request:Request,user=Depends(writer)):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>2*1024*1024:raise HTTPException(413,'Job request exceeds 2 MiB')
    try:
        body=json.loads(raw)
        if not isinstance(body,dict) or set(body)!={'files'}:raise ValueError
    except (ValueError,UnicodeError):raise HTTPException(400,'Expected JSON with files only') from None
    files=validate_files(body['files']);config=settings()
    with connection() as c:return enqueue(c,files,user,config)


@router.get('')
def list_jobs(user=Depends(identity)):
    with connection() as c:
        rows=c.execute('SELECT id,status,submitted_by,parent_id,batch_id,error,created_at,started_at,finished_at,settings FROM review_jobs ORDER BY created_at DESC,id LIMIT 100').fetchall()
    return [dict(zip(('job_id','status','submitted_by','parent_id','batch_id','error','created_at','started_at','finished_at','settings'),r)) for r in rows]


@router.get('/{job}')
def job_detail(job:str,user=Depends(identity)):
    with connection() as c:
        row=c.execute('SELECT id,status,submitted_by,parent_id,batch_id,error,created_at,started_at,finished_at,settings FROM review_jobs WHERE id=%s',(job,)).fetchone()
    if row is None:raise HTTPException(404,'Job not found')
    return dict(zip(('job_id','status','submitted_by','parent_id','batch_id','error','created_at','started_at','finished_at','settings'),row))


@router.post('/{job}/resubmit',status_code=202)
def resubmit(job:str,user=Depends(writer)):
    with connection() as c:
        row=c.execute('SELECT files,status FROM review_jobs WHERE id=%s',(job,)).fetchone()
        if row is None:raise HTTPException(404,'Job not found')
        if row[1] not in ('interrupted','failed'):raise HTTPException(409,'Only interrupted or failed jobs can be manually resubmitted')
        return enqueue(c,row[0],user,settings(),job)


def recover(c):
    # Running workers retain a session advisory lock. A dead worker loses it.
    rows=c.execute("SELECT id FROM review_jobs WHERE status='running' FOR UPDATE SKIP LOCKED").fetchall()
    for (job,) in rows:
        if c.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(job,)).fetchone()[0]:
            c.execute("UPDATE review_jobs SET status='interrupted',error='Worker disconnected; manual resubmission required',finished_at=now() WHERE id=%s AND status='running'",(job,))


def work_once(on_claim=None):
    from sql_optimization_agent.batch_sql import run_batch
    from sql_optimization_agent.sql_intake import recognize_query
    from sql_optimization_agent.service import OptimizationService
    from sql_optimization_agent.tools.sql_benchmark import json_value
    with connection() as c:
        with c.transaction():
            recover(c)
            row=c.execute("SELECT id,files,settings FROM review_jobs WHERE status='queued' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if row is None:return None
            job,files,config=row
            if not c.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(job,)).fetchone()[0]:return None
            c.execute("UPDATE review_jobs SET status='running',started_at=now() WHERE id=%s",(job,))
        # Commit claim before execution; session lock stays held across transactions.
        c.commit()
        try:
            if config.get('mode') not in ('simulated','live'):raise ValueError('Invalid stored model mode')
            if on_claim:on_claim(job)
            report=run_batch([str(i) for i in range(len(files))],service_factory=OptimizationService,
                fixture='normal' if config['mode']=='simulated' else None,use_qwen=True,
                query_reader=lambda i:recognize_query(files[int(i)]['content']))
            for row,f in zip(report['results'],files):row['file']=f['name']
            report=json.loads(json.dumps(report,ensure_ascii=False,default=json_value))
            validate(report);batch=report_id(report)
            raw=json.dumps(report,ensure_ascii=False,indent=2,default=json_value).encode()+b'\n'
            with c.transaction():
                inserted=c.execute('INSERT INTO review_batches(id,raw) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING id',(batch,raw)).fetchone()
                if inserted:
                    with c.cursor() as cursor:cursor.executemany('INSERT INTO review_entries(batch,entry) VALUES (%s,%s)',[(batch,r['index']) for r in report['results']])
                c.execute("UPDATE review_jobs SET status='completed',batch_id=%s,finished_at=now() WHERE id=%s",(batch,job))
            c.commit()
            return job
        except BaseException as error:
            c.rollback()
            c.execute("UPDATE review_jobs SET status=%s,error=%s,finished_at=now() WHERE id=%s",('interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed','Worker stopped; manual resubmission available',job));c.commit()
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            return job
        finally:
            c.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(job,));c.commit()
