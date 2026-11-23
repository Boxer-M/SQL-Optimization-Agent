"""Explicit test-data setup only; reuse conflict-preserving repository seed SQL."""
import os
from pathlib import Path
import psycopg
from psycopg import sql


def main():
    try:
        with psycopg.connect(host='postgres',port=5432,dbname='sql_optimization',user='sql_optimization_admin',password=os.environ['POSTGRES_PASSWORD']) as c:
            root=Path(__file__).resolve().parents[1]
            c.execute((root/'sql/seed_orders.sql').read_text().replace('\\set ON_ERROR_STOP on',''))
            c.execute('BEGIN')
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname='sql_optimization_reader'").fetchone() is None:
                c.execute(sql.SQL('CREATE ROLE sql_optimization_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}').format(sql.Literal(os.environ['POSTGRES_READER_PASSWORD'])))
            flags=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='sql_optimization_reader'").fetchone()
            if any(flags):raise ValueError('Unsafe existing reader role')
            c.execute('GRANT CONNECT ON DATABASE sql_optimization TO sql_optimization_reader')
            c.execute('GRANT USAGE ON SCHEMA public TO sql_optimization_reader')
            c.execute('GRANT SELECT ON public.orders TO sql_optimization_reader')
            c.commit()
            c.execute((root/'sql/seed_order_items.sql').read_text().replace('\\set ON_ERROR_STOP on',''))
        print('Test dataset ready; existing data retained');return 0
    except (psycopg.Error,KeyError,ValueError):print('Test data setup failed; no reset performed');return 1

if __name__=='__main__':raise SystemExit(main())
