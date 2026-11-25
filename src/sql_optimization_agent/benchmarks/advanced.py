"""Isolated reproducible benchmarks using existing comparison/measurement rules."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import math
from pathlib import Path
import platform

import psycopg
from psycopg import sql
import sqlglot

from sql_optimization_agent.db import readonly_connection
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.tools.sql_benchmark import _fetch, _compare, measure_pair
from sql_optimization_agent.tools.plan_fields import plan_node_fields
from .advanced_queries import (A_ORIGINAL,A_CANDIDATE,B_ORIGINAL,B_CANDIDATE,B_FANOUT,
                               B_DROPS_USERS,NARROW,WIDE,c_queries,a_fixture_queries)

MARKER='sql-agent-advanced-benchmark-only'
ROOT=Path(__file__).resolve().parents[3]
MAX_ROWS=120000
MAX_BYTES=64*1024*1024


def admin_connection(config):
    return psycopg.connect(host=config['POSTGRES_HOST'],port=int(config['POSTGRES_PORT']),
        dbname='sql_optimization',user='sql_optimization_admin',
        password=config['ADVANCED_BENCHMARK_ADMIN_PASSWORD'],connect_timeout=5)


def guard(connection,initialize=False):
    marker=connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
    if marker==MARKER:return
    tables=connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')").fetchone()[0]
    if not initialize or tables:
        raise ValueError('Refusing reset: database is not marked as an isolated advanced benchmark database')
    connection.execute(sql.SQL('COMMENT ON DATABASE sql_optimization IS {}').format(sql.Literal(MARKER)))


def seed_database(config,size,distribution,seed=20261125,initialize=False):
    if type(size) is not int or not 1<=size<=1000000 or distribution not in ('uniform','hot'):
        raise ValueError('Invalid deterministic seed parameters')
    if type(seed) is not int or not 0<=seed<=2147483647:raise ValueError('Invalid fixed seed')
    with admin_connection(config) as c:
        guard(c,initialize)
        c.execute('SELECT pg_advisory_xact_lock(6182309)')
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='sql_optimization_reader'").fetchone():
            c.execute(sql.SQL('CREATE ROLE sql_optimization_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}').format(sql.Literal(config['POSTGRES_READER_PASSWORD'])))
        flags=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='sql_optimization_reader'").fetchone()
        memberships=c.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='sql_optimization_reader'").fetchone()[0]
        if any(flags) or memberships:raise ValueError('Reader role has unexpected elevated privileges or memberships')
        source=(ROOT/'sql/seed_advanced_benchmarks.sql').read_text()
        source=source.replace('__ORDER_COUNT__',str(size)).replace('__SEED__',str(seed)).replace('__HOT__','1' if distribution=='hot' else '0')
        c.execute(source)
    return dataset_stats(config,size,distribution,seed)


def dataset_stats(config,size,distribution,seed):
    with readonly_connection(config) as c:
        counts={name:c.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(name))).fetchone()[0]
                for name in ('orders','order_items','users','products','refunds')}
        distribution_rows=c.execute('SELECT merchant_id,count(*) FROM public.orders WHERE order_id<900000000 GROUP BY merchant_id ORDER BY count(*) DESC,merchant_id').fetchall()
        stats=c.execute('SELECT count(DISTINCT customer_id),count(DISTINCT merchant_id),count(*) FILTER(WHERE created_at IS NULL),count(*) FILTER(WHERE amount IS NULL),min(created_at),max(created_at),sum(amount) FROM public.orders WHERE order_id<900000000').fetchone()
        groups=c.execute('SELECT count(*) FROM (SELECT u.region,p.category FROM public.orders o JOIN public.order_items i USING(order_id) JOIN public.products p USING(product_id) JOIN public.users u USING(customer_id) GROUP BY u.region,p.category) s').fetchone()[0]
        indexes=c.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname").fetchall()
        fingerprint=c.execute("SELECT md5(string_agg(checksum,',' ORDER BY block)) FROM (SELECT order_id/10000 AS block,md5(string_agg(concat_ws(':',order_id,customer_id,created_at,status,amount,merchant_id),'|' ORDER BY order_id)) AS checksum FROM public.orders GROUP BY block) s").fetchone()[0]
        return {'requested_base_orders':size,'distribution':distribution,'fixed_seed':seed,'row_counts':counts,
          'merchant_distribution':distribution_rows,'largest_merchant_share':distribution_rows[0][1]/size,
          'merchant_1_share':dict(distribution_rows).get(1,0)/size,
          'null_dimensions':dict(zip(('user_regions','product_categories','product_prices'),c.execute('SELECT (SELECT count(*) FROM public.users WHERE region IS NULL),(SELECT count(*) FROM public.products WHERE category IS NULL),(SELECT count(*) FROM public.products WHERE price IS NULL)').fetchone())),
          'customer_group_cardinality':stats[0],'merchant_group_cardinality':stats[1],
          'null_dates':stats[2],'null_order_amounts':stats[3],'base_date_min':stats[4],'base_date_max':stats[5],
          'base_amount_sum':stats[6],'region_category_group_cardinality':groups,
          'indexes':[dict(zip(('table','name','definition'),r)) for r in indexes],
          'order_fingerprint':fingerprint,'hand_fixture_orders':1,
          'distribution_scope':'base orders only; C excludes manual order 900000001'}


def plan_tree(node,path='0'):
    selected={**plan_node_fields(node),'path':path}
    for key in ('CTE Name','Subplan Name','Strategy','Partial Mode','Group Key','HashAgg Batches','Peak Memory Usage','Disk Usage','Hash Batches','Original Hash Batches','Hash Buckets'):
        if key in node:selected[key]=node[key]
    selected['children']=[plan_tree(child,f'{path}.{i}') for i,child in enumerate(node.get('Plans',[]))]
    return selected


def flatten(node):
    yield node
    for child in node.get('children',[]):yield from flatten(child)


def collect_plan(c,query,params):
    raw=c.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+query,params,prepare=False).fetchone()[0][0]
    tree=plan_tree(raw['Plan']);nodes=list(flatten(tree))
    return {'raw':raw,'tree':tree,
            'join_nodes':[n for n in nodes if 'Join Type' in n],
            'aggregate_nodes':[n for n in nodes if 'Aggregate' in n.get('Node Type','')],
            'sort_nodes':[n for n in nodes if 'Sort' in n.get('Node Type','')],
            'scan_leaf_order':[(n.get('Relation Name',n.get('CTE Name')),n.get('Alias'),n['path']) for n in nodes if n.get('Relation Name') or n.get('CTE Name')],
            'join_order_scope':'Actual executor outer/inner tree topology with node paths, not a wall-clock event trace; hash build and parallel scheduling may differ from leaf traversal order',
            'spill_observed':any(n.get('Temp Read Blocks',0)>0 or n.get('Temp Written Blocks',0)>0 or n.get('Disk Usage',0)>0 or n.get('Sort Space Type')=='Disk' for n in nodes),
            'root_temp_read_blocks':raw['Plan'].get('Temp Read Blocks',0),
            'root_temp_written_blocks':raw['Plan'].get('Temp Written Blocks',0)}


def sql_join_order(query):
    # Syntactic order is not a claim about optimizer execution order.
    tables=list(sqlglot.parse_one(query,read='postgres').find_all(sqlglot.exp.Table))
    return [{'table':table.sql(dialect='postgres'),'sql_offset':table.this.meta.get('start')} for table in sorted(tables,key=lambda t:t.this.meta.get('start',0))]


def p95(samples):
    if not samples:raise ValueError('Samples required')
    return sorted(samples)[math.ceil(.95*len(samples))-1]


def compare_pair(config,case_id,original,candidate,original_params=None,candidate_params=None,work_mem='4MB'):
    if work_mem not in ('64kB','4MB','64MB'):raise ValueError('Unsupported work_mem')
    result={'case_id':case_id,'evidence_source':'PostgreSQL measurement','sql':{'original':original,'candidate':candidate},
       'params':{'original':original_params,'candidate':candidate_params},'work_mem':work_mem,
       'sql_text_table_order':{'original':sql_join_order(original),'candidate':sql_join_order(candidate)},
       'candidate':{'sql':candidate},'status':'running','benchmark':None,'real_model_requests':0}
    with readonly_connection(config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        c.execute("SET LOCAL idle_in_transaction_session_timeout='60s'")
        c.execute(sql.SQL('SET LOCAL work_mem={}').format(sql.Literal(work_mem)))
        result['connection']=dict(zip(('database','role','read_only','isolation'),c.execute("SELECT current_database(),current_user,current_setting('transaction_read_only'),current_setting('transaction_isolation')").fetchone()))
        left=_fetch(c,original,MAX_ROWS,MAX_BYTES,original_params)
        right=_fetch(c,candidate,MAX_ROWS,MAX_BYTES,candidate_params)
        result['comparison']=_compare(left,right)
        result['comparison']['transfer_budget_usage']={'original_bytes':left.bytes_used,'candidate_bytes':right.bytes_used}
        if not result['comparison']['equivalent']:
            result.update(status='result_mismatch',timing_skipped='Full results or column metadata differ')
        else:
            result['benchmark']=measure_pair(c,candidate,candidate_params,original_sql=original,original_params=original_params)
            result['benchmark']['p95_ms']={name:p95(samples) for name,samples in result['benchmark']['execution_ms'].items()}
            result['benchmark']['percentile_method']='nearest rank; n=7 so P95 is maximum; not a stable tail-latency estimate'
            # Separate timing-on plans; not mixed into the seven measured samples.
            result['plans']={'original':collect_plan(c,original,original_params),'candidate':collect_plan(c,candidate,candidate_params)}
            result['status']='completed'
    return attach_recommendation(attach_assessment(result))


def manual_fixture(config):
    expected=Counter({(100001,'manual',Decimal('30'),Decimal('10')):1,(100002,None,Decimal('0'),Decimal('0')):1})
    results={}
    with readonly_connection(config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        for label,query,params in [('original',B_ORIGINAL,NARROW*2),('candidate',B_CANDIDATE,NARROW),('fanout',B_FANOUT,NARROW)]:
            got=_fetch(c,query+'\n'+('HAVING u.customer_id IN (100001,100002)' if label=='fanout' else 'WHERE u.customer_id IN (100001,100002)'),10,100000,params)
            results[label]=[{'row':list(r),'copies':n} for r,n in got.rows.items()]
            if label!='fanout' and got.rows!=expected:raise AssertionError('Manual 30/10 and no-trade 0/0 regression failed')
        assert results['original']==results['candidate'] or Counter(tuple(x['row']) for x in results['original'])==Counter(tuple(x['row']) for x in results['candidate'])
    return {'expected':[{'row':list(row),'copies':n} for row,n in expected.items()],
            'observed':results,'correct_queries_pass':True,'wrong_fanout_expected_sales':90,'wrong_fanout_expected_refund':20}


def a_semantic_fixture(config):
    expected=Counter({(None,None,Decimal('40'),3,1):1,(None,'all_null',None,1,1):1})
    original,candidate=a_fixture_queries()
    with readonly_connection(config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        left=_fetch(c,original,10,100000,NARROW)
        right=_fetch(c,candidate,10,100000,NARROW)
        comparison=_compare(left,right)
        assert comparison['equivalent'] and left.rows==right.rows==expected
    return {'evidence_source':'PostgreSQL read-only VALUES CTE semantic fixture; not performance samples',
            'expected':[{'row':list(row),'copies':copies} for row,copies in expected.items()],
            'comparison':comparison,'sql':{'original':original,'candidate':candidate},'params':NARROW}


def fixed_controls():
    rows=[]
    for name,left,right in [('overlap_lower_median',[8,10,12,9,11,10,10],[7,8,9,9,10,11,12]),('regressed',[1,2,3,2,2,2,2],[4,5,6,5,5,5,5])]:
        r=attach_recommendation(attach_assessment({'status':'completed','comparison':{'equivalent':True},'candidate':{'sql':'fixed controlled candidate'},'benchmark':{'execution_ms':{'original':left,'rewritten':right},'samples_per_query':7}}))
        assert r['recommendation']['decision']=='keep_original'
        rows.append({'case_id':name,'evidence_source':'fixed timing samples, not PostgreSQL measurement',**r})
    return rows


def set_index(config,enabled):
    with admin_connection(config) as c:
        guard(c)
        if enabled:c.execute('CREATE INDEX orders_merchant_experiment_idx ON public.orders(merchant_id)')
        else:c.execute('DROP INDEX IF EXISTS public.orders_merchant_experiment_idx')


def run_suite(config,save,*,sizes=(300000,1000000),seed=20261125,initialize=False):
    report={'report_type':'advanced_sql_benchmarks_v1','baseline_sha':None,'mode':'deterministic','real_model_requests':0,'datasets':[], 'cases':[],
            'budgets':{'result_rows':MAX_ROWS,'result_bytes':MAX_BYTES,'statement_timeout_ms':60000,'fetch_seconds':30},
            'method':'Complete multiset/metadata verification in one read-only repeatable-read snapshot per pair; then warm once and alternate 7 per query via existing measure_pair. Server execution time with TIMING OFF; no client transfer or throughput. Separate timing-on JSON plans.',
            'host':{'system':platform.system(),'machine':platform.machine(),'python':platform.python_version()},
            'fixed_controls':fixed_controls(),'status':'running'}
    with admin_connection(config) as c:
        guard(c)
        report['postgresql']=c.execute('SELECT version()').fetchone()[0]
        report['database_settings']={name:{'setting':value,'unit':unit} for name,value,unit in c.execute("SELECT name,setting,unit FROM pg_settings WHERE name IN ('max_parallel_workers_per_gather','shared_buffers','effective_cache_size','jit','hash_mem_multiplier','random_page_cost','seq_page_cost')").fetchall()}
    def execute(case_id,*args,**kwargs):
        report['active_case']=case_id;save(report)
        case=compare_pair(config,case_id,*args,**kwargs)
        case['dataset_index']=len(report['datasets'])-1
        return case
    def record(case):
        report['cases'].append(case);save(report)
        print(case['case_id'],case['status'],case['recommendation']['decision'],flush=True)
        if not case['case_id'].startswith('B_wrong_') and case['status']!='completed':
            raise AssertionError('Correct SQL pair failed semantic verification')
    for size in sizes:
        for distribution in ('uniform','hot'):
            report['active_stage']=f'seed_{size}_{distribution}';save(report)
            stats=seed_database(config,size,distribution,seed,initialize=initialize)
            report['datasets'].append(stats);save(report)
            print('Seeded',size,distribution,'orders',stats['row_counts']['orders'],flush=True)
            if size==sizes[0] and distribution=='uniform':
                repeated=seed_database(config,size,distribution,seed)
                report['repeat_seed']={'before':stats,'after':repeated,'identical':stats==repeated}
                assert stats==repeated
                for label,params in [('narrow',NARROW),('wide',WIDE)]:
                    record(execute('A_'+label,A_ORIGINAL,A_CANDIDATE,params,params))
                report['A_semantic_fixture']=a_semantic_fixture(config)
                report['manual_fixture']=manual_fixture(config)
                record(execute('B_correct',B_ORIGINAL,B_CANDIDATE,NARROW*2,NARROW))
                for label,query in [('fanout',B_FANOUT),('drops_users',B_DROPS_USERS)]:
                    case=execute('B_wrong_'+label,B_ORIGINAL,query,NARROW*2,NARROW)
                    assert case['status']=='result_mismatch' and case['benchmark'] is None
                    record(case)
                report['D_boundaries']={'source_case_ids':['A_narrow','A_wide'],'controlled_variables':'same dataset, indexes, 4MB work_mem and other database settings; only date range changes','additional_timing':False}
            for key in ('merchant_id','customer_id'):
                original,candidate=c_queries(key)
                case=execute(f'C_{size}_{distribution}_{key}',original,candidate)
                case['experiment']={'base_orders':size,'distribution':distribution,'group_key':key,'extra_index':False,'work_mem':'4MB'}
                record(case)
            if size==sizes[-1] and distribution=='hot':
                original,candidate=c_queries('merchant_id')
                set_index(config,True)
                case=execute('C_index_only',original,candidate)
                with readonly_connection(config) as c:case['index_definition']=c.execute("SELECT indexdef FROM pg_indexes WHERE indexname='orders_merchant_experiment_idx'").fetchone()[0]
                case['compare_to']=f'C_{size}_{distribution}_merchant_id'
                case['changed_variable']='only extra merchant B-tree index; table statistics unchanged'
                record(case);set_index(config,False)
                original,candidate=c_queries('customer_id')
                for mem in ('64kB','64MB'):
                    case=execute('C_work_mem_'+mem,original,candidate,work_mem=mem)
                    case['compare_to']=f'C_{size}_{distribution}_customer_id'
                    case['changed_variable']='only work_mem; no extra merchant index'
                    record(case)
    report['status']='completed';report.pop('active_case',None);report.pop('active_stage',None);save(report)
    return report
