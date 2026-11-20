"""One-time non-destructive migration for existing dedicated review_store databases."""
import os
from pathlib import Path
import psycopg


def main():
    if not all(os.environ.get(k) for k in ('REVIEW_DB_HOST','REVIEW_DB_PORT','REVIEW_ADMIN_PASSWORD')):
        print('Missing review migration environment');return 1
    try:
        with psycopg.connect(host=os.environ['REVIEW_DB_HOST'],port=os.environ['REVIEW_DB_PORT'],dbname='review_store',user='postgres',password=os.environ['REVIEW_ADMIN_PASSWORD'],connect_timeout=5) as c:
            c.execute((Path(__file__).resolve().parents[1]/'sql/init_batch_jobs.sql').read_text())
        print('Job storage initialized; existing reports/reviews preserved');return 0
    except psycopg.Error:
        print('Migration failed or already applied; no reset attempted');return 1

if __name__=='__main__':raise SystemExit(main())
