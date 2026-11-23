"""Repeatable review migration: preserve v0.1.0 bytes/events, never reset credentials."""
import os
from pathlib import Path
import psycopg
from psycopg import sql


def main():
    try:
        with psycopg.connect(host=os.environ['REVIEW_DB_HOST'],port=os.environ['REVIEW_DB_PORT'],dbname='review_store',user='postgres',password=os.environ['REVIEW_ADMIN_PASSWORD'],connect_timeout=5) as c:
            c.execute("SELECT pg_advisory_xact_lock(7823412)")
            role=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname='review_app'").fetchone()
            if role and any(role):raise ValueError('Unsafe existing application role')
            if role is None:c.execute(sql.SQL('CREATE ROLE review_app LOGIN PASSWORD {}').format(sql.Literal(os.environ['REVIEW_DB_PASSWORD'])))
            root=Path(__file__).resolve().parents[1]
            if c.execute("SELECT to_regclass('public.review_batches')").fetchone()[0] is None:c.execute((root/'sql/init_review_store.sql').read_text())
            if c.execute("SELECT to_regclass('public.review_jobs')").fetchone()[0] is None:c.execute((root/'sql/init_batch_jobs.sql').read_text())
            c.execute('SELECT id,raw,created_at FROM review_batches LIMIT 0')
            c.execute('SELECT batch,entry,version,decision,reviewer,reason FROM review_events LIMIT 0')
            c.execute('SELECT id,files,settings,status,parent_id,batch_id FROM review_jobs LIMIT 0')
        with psycopg.connect(host=os.environ['REVIEW_DB_HOST'],port=os.environ['REVIEW_DB_PORT'],dbname='review_store',user='review_app',password=os.environ['REVIEW_DB_PASSWORD'],connect_timeout=5) as c:c.execute('SELECT id FROM review_jobs LIMIT 0')
        print('Review migration successful; existing reports, reviews and credentials retained');return 0
    except (psycopg.Error,KeyError,ValueError):
        print('Review migration failed; check dedicated store and existing credentials; no reset performed');return 1

if __name__=='__main__':raise SystemExit(main())
