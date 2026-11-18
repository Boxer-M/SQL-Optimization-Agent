"""Standalone fixed-data acceptance benchmark, not an Agent/service scenario."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values
import psycopg

from sql_optimization_agent.db import DatabaseConfigurationError, DatabaseConnectionError, readonly_connection
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.tools.sql_benchmark import (
    BenchmarkValidationError, _compare, _fetch, _timeout_errors, json_value, measure_pair,
)


DAY_FILTER = "o.created_at >= %s::timestamp AND o.created_at < %s::timestamp"
JOIN_FROM = "FROM public.orders AS o JOIN public.order_items AS i ON i.order_id = o.order_id"
EXISTS_FILTER = "EXISTS (SELECT 1 FROM public.order_items AS i WHERE i.order_id = o.order_id)"
JOIN_COUNT = f"SELECT COUNT(DISTINCT o.order_id) AS order_count\n{JOIN_FROM}\nWHERE {DAY_FILTER}"
EXISTS_COUNT = f"SELECT COUNT(*) AS order_count\nFROM public.orders AS o\nWHERE {DAY_FILTER} AND {EXISTS_FILTER}"
JOIN_IDS = f"SELECT DISTINCT o.order_id\n{JOIN_FROM}\nWHERE {DAY_FILTER}"
EXISTS_IDS = f"SELECT o.order_id\nFROM public.orders AS o\nWHERE {DAY_FILTER} AND {EXISTS_FILTER}"


def run(config):
    context = OrderDateContext.parse('2025-06-15')
    params = (context.start, context.end)
    report = {"source": "fixed_synthetic_data_real_postgresql_no_model",
              "measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "date_context": context.as_dict(), "benchmark": None,
              "sql": {"join_count_distinct": JOIN_COUNT, "exists_count": EXISTS_COUNT,
                      "join_order_ids": JOIN_IDS, "exists_order_ids": EXISTS_IDS},
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
                   sum(item_count)::bigint
            FROM day_counts
        """, params).fetchone()
        report['data'] = {'items_total':total, **dict(zip(('day_orders','zero_items','one_item','multiple_items','day_item_rows'),groups))}
        if not all(groups[index] > 0 for index in (1,2,3)):
            raise BenchmarkValidationError('Selected day must contain zero-, one- and multiple-item orders')
        left = _fetch(connection, JOIN_IDS, 10000, 16*1024*1024, params)
        right = _fetch(connection, EXISTS_IDS, 10000, 16*1024*1024, params)
        comparison = _compare(left,right)
        join_ids = sorted(row[0] for row in left.rows)
        exists_ids = sorted(row[0] for row in right.rows)
        report['membership'] = {'comparison':comparison, 'join_order_ids':join_ids, 'exists_order_ids':exists_ids,
                                'join_minus_exists':sorted(set(join_ids)-set(exists_ids)),
                                'exists_minus_join':sorted(set(exists_ids)-set(join_ids))}
        if not comparison['equivalent']:
            report.update(status='membership_mismatch',reason='Order membership differs; counting and timing skipped')
            return report
        join_count = connection.execute(JOIN_COUNT,params).fetchone()[0]
        exists_count = connection.execute(EXISTS_COUNT,params).fetchone()[0]
        report['counts'] = {'join_count_distinct':join_count,'exists_count':exists_count}
        if join_count != exists_count or join_count != len(join_ids):
            report.update(status='count_mismatch',reason='Count differs from verified membership; timing skipped')
            return report
        report['benchmark_labels'] = {'original':'join_count_distinct','rewritten':'exists_count'}
        report['benchmark'] = measure_pair(connection,EXISTS_COUNT,params,original_sql=JOIN_COUNT,original_params=params)
        report.update(status='completed',reason='在本次测试数据上结果一致')
    return report


def main():
    if len(sys.argv) != 1:
        print('This fixed acceptance script accepts no arguments.',file=sys.stderr)
        return 2
    try:
        config = {**dotenv_values(Path(__file__).resolve().parents[1]/'.env',interpolate=False),**os.environ}
        report = run(config)
        print(json.dumps(report,ensure_ascii=False,indent=2,default=json_value))
        return 0 if report['status']=='completed' else 1
    except (DatabaseConfigurationError,DatabaseConnectionError,BenchmarkValidationError) as error:
        print(str(error),file=sys.stderr)
        return 1
    except OSError:
        print('Unable to read configuration or access the database.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
