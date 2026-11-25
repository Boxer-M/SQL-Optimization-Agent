"""Initialize the isolated, exactly-million-order customer report fixture."""
import argparse,json
from pathlib import Path
from dotenv import dotenv_values
from psycopg import sql
from sql_optimization_agent.benchmarks.advanced import seed_database,admin_connection,guard
from sql_optimization_agent.db import readonly_connection
from sql_optimization_agent.tools.sql_benchmark import json_value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',required=True)
    parser.add_argument('--initialize',action='store_true');parser.add_argument('--report',default='reports/customer_metrics_dataset.json')
    args=parser.parse_args();config=dotenv_values(args.env_file,interpolate=False)
    base=seed_database(config,999992,'uniform',20261125,initialize=args.initialize)
    with admin_connection(config) as c:
        guard(c)
        c.execute(Path('sql/seed_customer_metrics.sql').read_text())
    # Visibility map allows the original's reasonable covering index to work fairly.
    with admin_connection(config) as c:
        c.autocommit=True
        c.execute('VACUUM (ANALYZE) public.orders');c.execute('VACUUM (ANALYZE) public.users')
    with readonly_connection(config) as c:
        total=c.execute('SELECT count(*) FROM public.orders').fetchone()[0]
        assert total==1000000
        users=c.execute('SELECT count(*) FROM public.users').fetchone()[0]
        histogram=c.execute("SELECT n,count(*) FROM (SELECT u.customer_id,count(o.order_id) AS n FROM public.users u LEFT JOIN public.orders o ON o.customer_id=u.customer_id AND o.created_at >= timestamp '2025-01-01' AND o.created_at < timestamp '2026-01-01' GROUP BY u.customer_id) t GROUP BY n ORDER BY n").fetchall()
        stats=c.execute("SELECT count(DISTINCT customer_id),count(*) FILTER(WHERE amount IS NULL),count(*) FILTER(WHERE created_at IS NULL),min(created_at),max(created_at) FROM public.orders").fetchone()
        indexes=c.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename IN ('orders','users') ORDER BY tablename,indexname").fetchall()
        settings=c.execute("SELECT name,setting,unit FROM pg_settings WHERE name IN ('jit','work_mem','max_parallel_workers_per_gather','shared_buffers','effective_cache_size','hash_mem_multiplier')").fetchall()
        fingerprint=c.execute("SELECT md5(string_agg(checksum,',' ORDER BY bucket)) FROM (SELECT order_id/10000 AS bucket,md5(string_agg(ROW(order_id,customer_id,created_at,status,amount,merchant_id)::text,'|' ORDER BY order_id)) AS checksum FROM public.orders GROUP BY bucket) s").fetchone()[0]
        users_fingerprint=c.execute("SELECT md5(string_agg(ROW(customer_id,region)::text,'|' ORDER BY customer_id)) FROM public.users").fetchone()[0]
        report={'order_fingerprint':fingerprint,'user_fingerprint':users_fingerprint,'status':'completed','seed':20261125,'seed_version':'customer-metrics-v1','base_orders':999992,'fixture_orders':8,
                'total_orders':total,'total_customers':users,'customers_with_any_orders':stats[0],
                'null_amount_orders':stats[1],'null_timestamp_orders':stats[2],'min_timestamp':stats[3],'max_timestamp':stats[4],
                'annual_orders_per_customer_histogram':histogram,
                'merchant_distribution':base['merchant_distribution'],'customer_distribution':'100000 balanced synthetic customer IDs plus four semantic-sentinel users',
                'indexes':[dict(zip(('table','name','definition'),row)) for row in indexes],
                'postgresql':c.execute('SELECT version()').fetchone()[0],
                'settings':{name:{'setting':value,'unit':unit} for name,value,unit in settings},
                'semantics':{'count':'all date-qualified orders','revenue':'SUM(amount) FILTER paid, no COALESCE','last_order':'MAX(date-qualified created_at)'},
                'real_model_requests':0}
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=json_value)+'\n')
    print(json.dumps({'status':'completed','orders':total,'customers':users,'report':args.report}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        import sys
        print('Customer dataset initialization failed: '+type(error).__name__+'; no driver details or credentials emitted',file=sys.stderr)
        raise SystemExit(1) from None
