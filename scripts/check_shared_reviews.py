"""Isolated real HTTP/PostgreSQL concurrency acceptance, no optimizer access."""
import os,json,secrets,socket,subprocess,time,sys,tempfile,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
ROOT=Path(__file__).resolve().parents[1]

def main():
    output=ROOT/'reports/shared_batch_review_acceptance.json'
    if output.exists():raise FileExistsError('Acceptance report already exists')
    def port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    pgport,httpport=port(),port();name='review-acceptance-'+uuid.uuid4().hex[:8]
    admin,app=secrets.token_hex(32),secrets.token_hex(32)
    tokens={secrets.token_hex(32):{'name':n,'role':r} for n,r in [('viewer','viewer'),('alice','reviewer'),('bob','reviewer')]}
    keys=list(tokens)
    env={**os.environ,'POSTGRES_PASSWORD':admin}
    subprocess.run(['docker','run','-d','--name',name,'-e','POSTGRES_PASSWORD','-e','POSTGRES_DB=review_store','-p',f'127.0.0.1:{pgport}:5432','postgres:16'],env=env,check=True,stdout=subprocess.DEVNULL)
    server=None
    try:
        for _ in range(60):
            if subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
            time.sleep(.5)
        sql=f"CREATE ROLE review_app LOGIN PASSWORD '{app}';\n"+(ROOT/'sql/init_review_store.sql').read_text()
        subprocess.run(['docker','exec','-i',name,'psql','-U','postgres','-d','review_store','-v','ON_ERROR_STOP=1'],input=sql.encode(),check=True,stdout=subprocess.DEVNULL)
        env.update(REVIEW_DB_HOST='127.0.0.1',REVIEW_DB_PORT=str(pgport),REVIEW_DB_NAME='review_store',REVIEW_DB_USER='review_app',REVIEW_DB_PASSWORD=app,REVIEW_TOKENS=json.dumps(tokens))
        # Guards run inside the actual HTTP server process.
        bootstrap="import sql_optimization_agent.db as d; import sql_optimization_agent.qwen_workflow as q; import sql_optimization_agent.order_items_qwen as i; "
        bootstrap+="import psycopg; original_connect=psycopg.connect\n"
        bootstrap+="def guarded_connect(*a,**k):\n    if a or k.get('dbname')!='review_store': raise AssertionError('Target DB forbidden')\n    return original_connect(**k)\n"
        bootstrap+="psycopg.connect=guarded_connect\n"
        bootstrap+="def forbidden(*a,**k): raise AssertionError('Optimizer/model access forbidden')\n"
        bootstrap+="d.readonly_connection=forbidden;q.readonly_connection=forbidden;q.request_suggestion=forbidden;i.request_suggestion=forbidden\n"
        bootstrap+=f"import uvicorn;uvicorn.run('sql_optimization_agent.api:app',host='127.0.0.1',port={httpport},log_level='warning')"
        with tempfile.TemporaryFile() as logs:
            server=subprocess.Popen([sys.executable,'-c',bootstrap],cwd=ROOT,env=env,stdout=logs,stderr=logs)
            with httpx.Client(base_url=f'http://127.0.0.1:{httpport}',timeout=30,trust_env=False) as c:
                for _ in range(100):
                    try:
                        if c.get('/openapi.json').status_code==200:break
                    except httpx.ConnectError:pass
                    time.sleep(.1)
                auth=lambda k:{'Authorization':'Bearer '+k}
                assert c.get('/reviews/batches').status_code==401
                raw=(ROOT/'reports/batch_sql_diagnosis_acceptance.json').read_bytes()
                assert c.post('/reviews/batches',content=raw,headers=auth(keys[0])).status_code==403
                def imp(_):return c.post('/reviews/batches',content=raw,headers=auth(keys[1]))
                with ThreadPoolExecutor(2) as pool:imports=list(pool.map(imp,range(2)))
                assert all(r.status_code==200 for r in imports)
                assert sorted(r.json()['existing'] for r in imports)==[False,True]
                batch=imports[0].json()['batch_id'];base='/reviews/batches/'+batch
                assert len(c.get('/reviews/batches',headers=auth(keys[0])).json())==1
                before=c.get(base,headers=auth(keys[0])).json()
                def submit(k):return c.post(base+'/entries/2/reviews',headers=auth(k),json={'version':0,'decision':'defer','reason':'Independent synthetic-data review'})
                with ThreadPoolExecutor(2) as pool:responses=list(pool.map(submit,keys[1:]))
                assert sorted(r.status_code for r in responses)==[200,409]
                loser=keys[1:][next(i for i,r in enumerate(responses) if r.status_code==409)]
                history=c.get(base+'/entries/2',headers=auth(loser)).json();assert history['version']==1
                retry=c.post(base+'/entries/2/reviews',headers=auth(loser),json={'version':1,'decision':'accept','reason':'Reread current version and verified candidate'})
                assert retry.status_code==200
                final=c.get(base+'/entries/2',headers=auth(keys[0])).json();assert [r['version'] for r in final['reviews']]==[1,2]
                assert c.get(base+'/raw',headers=auth(keys[0])).content==raw
                assert c.get(base,headers=auth(keys[0])).json()==before
                assert c.post(base+'/entries/1/reviews',headers=auth(keys[1]),json={'version':0,'decision':'accept','reason':'negative control'}).status_code==400
                report={'source':'Actual HTTP and isolated PostgreSQL review_store using restricted review_app',
                    'container':name,'unauthenticated':401,'viewer_write':403,'imports':[r.json() for r in imports],
                    'initial_review_statuses':[r.status_code for r in responses],'reread_version':history['version'],
                    'final_history':final,'original_bytes_unchanged':True,'original_report_unchanged':True,
                    'optimizer_and_model_access':'Server-side guards raise on target connection or model calls; all requests passed',
                    'new_real_model_requests':0,'target_database_connections':0}
                text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
                assert not any(secret in text for secret in [admin,app,*keys])
                output.write_text(text)
                print('Shared review acceptance passed: 401,403, unique import, 200/409, versions 1→2, raw unchanged')
    finally:
        if server:server.terminate();server.wait(timeout=10)
        subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,check=True)

if __name__=='__main__':main()
