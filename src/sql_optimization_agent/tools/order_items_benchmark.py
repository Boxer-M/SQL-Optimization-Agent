"""Shared controlled JOIN/EXISTS membership and count validation; never calls a model."""

from datetime import datetime, timezone

import psycopg

from sql_optimization_agent.db import readonly_connection
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import (
    BenchmarkValidationError, _compare, _fetch, _timeout_errors, measure_pair,
)

SCENARIO = "orders_with_items_count"
FIXTURES = ("normal", "swapped-ids")

DAY_FILTER = "o.created_at >= %s::timestamp AND o.created_at < %s::timestamp"
JOIN_FROM = "FROM public.orders AS o JOIN public.order_items AS i ON i.order_id = o.order_id"
EXISTS_FILTER = "EXISTS (SELECT 1 FROM public.order_items AS i WHERE i.order_id = o.order_id)"
JOIN_COUNT = f"SELECT COUNT(DISTINCT o.order_id) AS order_count\n{JOIN_FROM}\nWHERE {DAY_FILTER}"
EXISTS_COUNT = f"SELECT COUNT(*) AS order_count\nFROM public.orders AS o\nWHERE {DAY_FILTER} AND {EXISTS_FILTER}"
JOIN_IDS = f"SELECT DISTINCT o.order_id\n{JOIN_FROM}\nWHERE {DAY_FILTER}"
EXISTS_IDS = f"SELECT o.order_id\nFROM public.orders AS o\nWHERE {DAY_FILTER} AND {EXISTS_FILTER}"

# Negative control: remove one eligible ID, insert one no-item ID on the same day.
# These are application-owned SQL templates, not user/model-supplied SQL.
SWAPPED_IDS = f"""WITH day_orders AS (
    SELECT o.order_id FROM public.orders AS o WHERE {DAY_FILTER}
), matched AS (
    SELECT o.order_id FROM day_orders AS o WHERE {EXISTS_FILTER}
)
SELECT order_id FROM matched WHERE order_id <> (SELECT min(order_id) FROM matched)
UNION ALL
SELECT min(o.order_id) FROM day_orders AS o WHERE NOT {EXISTS_FILTER} HAVING count(*) > 0"""


def run(config, context: OrderDateContext, *, fixture: str = 'normal', require_coverage: bool = False):
    if fixture not in FIXTURES:
        raise BenchmarkValidationError('Unsupported order-items controlled candidate')
    candidate_ids = EXISTS_IDS if fixture == 'normal' else SWAPPED_IDS
    candidate_count = EXISTS_COUNT if fixture == 'normal' else f'SELECT COUNT(*) AS order_count FROM ({SWAPPED_IDS}) AS candidate_orders'
    params = (context.start, context.end)
    report = {"source": "controlled_candidate_real_postgresql_no_model", "scenario": SCENARIO,
              "fixture": fixture, "model_requests": 0, "target_date": context.target.isoformat(),
              "stage": "result_validation",
              "measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "date_context": context.as_dict(), "benchmark": None,
              "sql": {"join_count_distinct": JOIN_COUNT, "candidate_count": candidate_count,
                      "join_order_ids": JOIN_IDS, "candidate_order_ids": candidate_ids},
              "candidate": {"kind": 'exists' if fixture == 'normal' else 'controlled_swapped_ids',
                            "sql": candidate_count, "order_ids_sql": candidate_ids},
              "parameters": [p.isoformat(sep=' ') for p in params]}
    with readonly_connection(config) as connection, _timeout_errors():
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        identity = connection.execute(
            "SELECT current_database(), current_user, current_setting('transaction_read_only'), "
            "current_setting('transaction_isolation'), version()"
        ).fetchone()
        report['connection'] = dict(zip(('database','user','read_only','isolation','version'),identity))
        total = connection.execute('SELECT count(*) FROM public.order_items').fetchone()[0]
        groups = connection.execute(f"""
            WITH day_counts AS (
                SELECT o.order_id, count(i.item_id) AS item_count
                FROM public.orders AS o LEFT JOIN public.order_items AS i ON i.order_id = o.order_id
                WHERE {DAY_FILTER} GROUP BY o.order_id
            )
            SELECT count(*), count(*) FILTER (WHERE item_count = 0),
                   count(*) FILTER (WHERE item_count = 1), count(*) FILTER (WHERE item_count > 1),
                   COALESCE(sum(item_count),0)::bigint
            FROM day_counts
        """, params).fetchone()
        report['data'] = {'items_total':total, **dict(zip(('day_orders','zero_items','one_item','multiple_items','day_item_rows'),groups))}
        if require_coverage and not all(groups[index] > 0 for index in (1,2,3)):
            raise BenchmarkValidationError('Selected day must contain zero-, one- and multiple-item orders')
        left = _fetch(connection, JOIN_IDS, 10000, 16*1024*1024, params)
        right = _fetch(connection, candidate_ids, 10000, 16*1024*1024, params)
        comparison = _compare(left,right)
        join_ids = sorted(row[0] for row in left.rows)
        proposed_ids = sorted(row[0] for row in right.rows)
        missing = sorted(set(join_ids)-set(proposed_ids))
        extra = sorted(set(proposed_ids)-set(join_ids))
        report['membership'] = {'comparison':comparison, 'join_order_ids':join_ids, 'candidate_order_ids':proposed_ids,
                                'join_minus_candidate':missing, 'candidate_minus_join':extra,
                                'join_minus_candidate_count':len(missing), 'candidate_minus_join_count':len(extra)}
        join_count = connection.execute(JOIN_COUNT,params).fetchone()[0]
        proposed_count = connection.execute(candidate_count,params).fetchone()[0]
        report['counts'] = {'join_count_distinct':join_count,'candidate_count':proposed_count}
        if not comparison['equivalent']:
            report.update(status='membership_mismatch',reason='Order IDs differ, even if final counts match; timing skipped')
            return report
        if join_count != proposed_count or join_count != len(join_ids) or proposed_count != right.count:
            report.update(status='count_mismatch',reason='Count differs from verified membership; timing skipped')
            return report
        report['benchmark_labels'] = {'original':'join_count_distinct','rewritten':'candidate_count'}
        report['stage'] = 'benchmark'
        report['benchmark'] = measure_pair(connection,candidate_count,params,original_sql=JOIN_COUNT,original_params=params)
        report.update(status='completed',stage='complete',reason='在本次测试数据上结果一致')
    return report
