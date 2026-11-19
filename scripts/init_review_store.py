"""Initialize a NEW dedicated review database, never the optimization database."""
import os
from pathlib import Path
import psycopg
from psycopg import sql


def main():
    required=['REVIEW_DB_HOST','REVIEW_DB_PORT','REVIEW_DB_PASSWORD','REVIEW_ADMIN_PASSWORD']
    if not all(os.environ.get(name) for name in required):
        print('Missing review initialization environment variables');return 1
    try:
        with psycopg.connect(host=os.environ['REVIEW_DB_HOST'],port=os.environ['REVIEW_DB_PORT'],dbname='review_store',user='postgres',password=os.environ['REVIEW_ADMIN_PASSWORD'],connect_timeout=5) as c:
            c.execute(sql.SQL('CREATE ROLE review_app LOGIN PASSWORD {}').format(sql.Literal(os.environ['REVIEW_DB_PASSWORD'])))
            c.execute((Path(__file__).resolve().parents[1]/'sql/init_review_store.sql').read_text())
        print('Review store initialized; application uses review_app, not postgres');return 0
    except psycopg.Error:
        print('Review initialization failed; inspect dedicated database/configuration (no reset attempted)');return 1

if __name__=='__main__':raise SystemExit(main())
