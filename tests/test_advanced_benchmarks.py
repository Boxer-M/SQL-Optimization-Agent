"""Pure regression plus opt-in real PostgreSQL semantic checks, never model calls."""
from collections import Counter
from decimal import Decimal
import os

import pytest
import psycopg

_REAL_CONNECT=psycopg.connect
from dotenv import dotenv_values
from sql_optimization_agent.benchmarks.advanced import (
    p95, fixed_controls, sql_join_order, plan_tree, flatten, guard,
    compare_pair, manual_fixture, MAX_BYTES, MAX_ROWS,
)
from sql_optimization_agent.benchmarks.advanced_queries import (
    A_ORIGINAL,A_CANDIDATE,B_ORIGINAL,B_CANDIDATE,B_FANOUT,B_DROPS_USERS,NARROW,WIDE,c_queries,
)
from sql_optimization_agent.tools.sql_benchmark import QueryResult,_compare


def test_comparison_keeps_null_decimal_and_duplicate_multiplicity():
    columns=[('region',25,None,None,None),('amount',1700,None,None,None)]
    row=(None,Decimal('10.20'))
    left=QueryResult(columns,Counter({row:2}),2,100)
    right=QueryResult(columns,Counter({row:1}),1,50)
    assert not _compare(left,right)['equivalent']
    assert _compare(left,left)['equivalent']
    right.columns=[('region',25,None,None,None),('amount',701,None,None,None)]
    assert not _compare(left,right)['columns_equal']


def test_conservative_fixed_timing_controls():
    rows=fixed_controls()
    assert rows[0]['performance_assessment']['median_difference_ms']<0
    assert rows[0]['performance_assessment']['verdict']=='inconclusive'
    assert rows[1]['performance_assessment']['verdict']=='regressed'
    assert all(r['recommendation']['decision']=='keep_original' for r in rows)
    assert p95([7,1,2,6,4,5,3])==7


def test_sql_join_order_is_source_order_not_ast_breadth_order():
    order=sql_join_order(A_ORIGINAL)
    assert [x['table'] for x in order]==['public.orders AS o','public.order_items AS i','public.products AS p','public.users AS u']
    assert [x['sql_offset'] for x in order]==sorted(x['sql_offset'] for x in order)
    with pytest.raises(ValueError):c_queries('merchant_id; DROP TABLE orders')


def test_plan_evidence_preserves_tree_loops_and_spill():
    raw={'Node Type':'Aggregate','Strategy':'Hashed','Group Key':['x'],'HashAgg Batches':4,'Disk Usage':123,
         'Actual Rows':12,'Actual Loops':2,'Temp Read Blocks':20,
         'Plans':[{'Node Type':'Sort','Sort Method':'external merge','Sort Space Type':'Disk','Actual Loops':1}]}
    tree=plan_tree(raw)
    assert tree['Disk Usage']==123 and tree['Actual Loops']==2
    assert list(flatten(tree))[1]['path']=='0.0'
    assert tree['children'][0]['Sort Space Type']=='Disk'


def test_unmarked_database_cannot_be_reset():
    class Cursor:
        def __init__(self,value):self.value=value
        def fetchone(self):return (self.value,)
    class Connection:
        def __init__(self):self.calls=[]
        def execute(self,statement):
            self.calls.append(statement)
            return Cursor(None if len(self.calls)==1 else 5)
    c=Connection()
    with pytest.raises(ValueError,match='Refusing reset'):guard(c,initialize=True)
    assert len(c.calls)==2  # No COMMENT, TRUNCATE, CREATE or other mutation.


@pytest.fixture
def live_config(monkeypatch,block_external_services):
    path=os.environ.get('ADVANCED_BENCHMARK_ENV')
    if not path:pytest.skip('Set ADVANCED_BENCHMARK_ENV for isolated PostgreSQL semantic regression')
    class Secret(str):
        def __repr__(self):return '<redacted>'
    class Config(dict):
        def __repr__(self):return '<isolated PostgreSQL configuration; credentials redacted>'
    config=Config(dotenv_values(path))
    for name in ('POSTGRES_READER_PASSWORD','ADVANCED_BENCHMARK_ADMIN_PASSWORD'):
        config[name]=Secret(config[name])
    def isolated_connect(*args,**kwargs):
        assert not args
        assert kwargs['host']==config['POSTGRES_HOST'] and kwargs['port']==int(config['POSTGRES_PORT'])
        assert kwargs['dbname']=='sql_optimization' and kwargs['user']=='sql_optimization_reader'
        connection=_REAL_CONNECT(**kwargs)
        from sql_optimization_agent.benchmarks.advanced import MARKER
        marker=connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        connection.commit()
        if marker!=MARKER:
            connection.close();raise AssertionError('Integration test requires marked isolated benchmark database')
        return connection
    # Narrow opt-in override: the shared offline guard still blocks HTTP and every other test.
    monkeypatch.setattr(psycopg,'connect',isolated_connect)
    return config


def test_postgresql_manual_two_lines_three_refunds_and_no_trade(live_config):
    result=manual_fixture(live_config)
    assert result['correct_queries_pass']
    rows={r['row'][0]:r['row'] for r in result['observed']['fanout']}
    assert rows[100001][2:]==[Decimal('90'),Decimal('20')]
    assert rows[100002][2:]==[Decimal('0'),Decimal('0')]


@pytest.mark.parametrize('wrong',[B_FANOUT,B_DROPS_USERS])
def test_postgresql_rejects_wrong_candidates_before_timing(live_config,wrong,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('Mismatched SQL reached performance sampling')
    monkeypatch.setattr('sql_optimization_agent.benchmarks.advanced.measure_pair',forbidden)
    result=compare_pair(live_config,'negative_semantics',B_ORIGINAL,wrong,NARROW*2,NARROW)
    assert result['status']=='result_mismatch'
    assert result['benchmark'] is None
    assert result['recommendation']['decision']=='keep_original'


@pytest.mark.parametrize('params',[NARROW,WIDE])
def test_postgresql_a_group_reconciliation(live_config,params,monkeypatch):
    # Read-only semantic regression; do not repeat the expensive timing suite in pytest.
    from sql_optimization_agent.db import readonly_connection
    from sql_optimization_agent.tools.sql_benchmark import _fetch
    import psycopg
    with readonly_connection(live_config) as c:
        c.isolation_level=psycopg.IsolationLevel.REPEATABLE_READ
        c.execute("SET LOCAL statement_timeout='60s'")
        left=_fetch(c,A_ORIGINAL,MAX_ROWS,MAX_BYTES,params)
        right=_fetch(c,A_CANDIDATE,MAX_ROWS,MAX_BYTES,params)
        assert _compare(left,right)['equivalent']
        assert any(row[0] is None or row[1] is None for row in left.rows)


@pytest.mark.parametrize('size,distribution,seed',[(0,'uniform',1),(1000001,'uniform',1),(True,'uniform',1),(10,'unknown',1),(10,'uniform',-1)])
def test_invalid_seed_never_connects(size,distribution,seed,monkeypatch):
    from sql_optimization_agent.benchmarks.advanced import seed_database
    def forbidden(*args,**kwargs):pytest.fail('Invalid seed parameters attempted database connection')
    monkeypatch.setattr('sql_optimization_agent.benchmarks.advanced.admin_connection',forbidden)
    with pytest.raises(ValueError):seed_database({},size,distribution,seed)


def test_postgresql_a_repeated_category_distinct_orders_and_all_null_sum(live_config):
    from sql_optimization_agent.benchmarks.advanced import a_semantic_fixture
    result=a_semantic_fixture(live_config)
    assert result['comparison']['equivalent']
    assert result['comparison']['original_rows']==2
    assert result['expected'][0]['row']==[None,None,Decimal('40'),3,1]
    assert result['expected'][1]['row']==[None,'all_null',None,1,1]
