"""Bounded correctness checks and natural-plan benchmarks for fixed SQL only."""

from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import pickle
from statistics import median
from time import monotonic

import psycopg

from sql_optimization_agent.db import readonly_connection
from sql_optimization_agent.tools.plan_fields import plan_node_fields


ORIGINAL = """SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at::date = DATE '2025-06-15'"""
REWRITTEN = """SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND created_at < TIMESTAMP '2025-06-16 00:00:00'"""
WRONG_UPPER_BOUND = """SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND created_at <= TIMESTAMP '2025-06-16 00:00:00'"""
EXPLAIN = "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) "


class BenchmarkValidationError(RuntimeError):
    """A bounded validation failed; no performance comparison should follow."""


@contextmanager
def _timeout_errors():
    try:
        yield
    except psycopg.errors.QueryCanceled:
        raise BenchmarkValidationError(
            "Statement exceeded 5-second timeout; validation and benchmark stopped"
        ) from None


@dataclass
class QueryResult:
    columns: list[tuple]
    rows: Counter
    count: int
    bytes_used: int


def _fetch(connection, query: str, max_rows: int, max_bytes: int, params=None) -> QueryResult:
    # A server cursor avoids buffering an unbounded result before checking budgets.
    deadline = monotonic() + 30
    rows = Counter()
    count = size = 0
    with connection.cursor(name="benchmark_results") as cursor:
        cursor.execute(query, params)
        columns = [
            (c.name, c.type_code, c.internal_size, c.precision, c.scale)
            for c in cursor.description
        ]
        while True:
            if monotonic() > deadline:
                raise BenchmarkValidationError("Result retrieval exceeded 30-second budget")
            batch = cursor.fetchmany(min(256, max_rows - count + 1))
            if monotonic() > deadline:
                raise BenchmarkValidationError("Result retrieval exceeded 30-second budget")
            if not batch:
                break
            for row in batch:
                count += 1
                size += len(pickle.dumps(row, protocol=5))
                if count > max_rows or size > max_bytes:
                    raise BenchmarkValidationError(
                        "Result exceeds row/byte budget; comparison and benchmark stopped"
                    )
                # Keep native None, datetime and Decimal values; never use set(rows).
                rows[row] += 1
    return QueryResult(columns, rows, count, size)


def _compare(left: QueryResult, right: QueryResult) -> dict:
    same_columns = left.columns == right.columns
    same_rows = left.rows == right.rows
    def differences(counter):
        return [{"row": list(row), "copies": copies} for row, copies in list(counter.items())[:5]]
    return {
        "original_rows": left.count,
        "candidate_rows": right.count,
        "columns_equal": same_columns,
        "rows_and_multiplicities_equal": same_rows,
        "equivalent": same_columns and same_rows,
        "reason": "在本次测试数据上结果一致" if same_columns and same_rows
                  else "Column metadata or full row multiplicities differ; performance test skipped",
        "missing_from_candidate": differences(left.rows - right.rows),
        "extra_in_candidate": differences(right.rows - left.rows),
        "columns": left.columns,
    }


def _plan_nodes(node: dict) -> list[dict]:
    result = [plan_node_fields(node)]
    for child in node.get("Plans", []):
        result.extend(_plan_nodes(child))
    return result


def _explain(connection, query: str, params=None) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(EXPLAIN + query, params, prepare=False)
        plan = cursor.fetchone()[0][0]
    return {
        "execution_ms": plan["Execution Time"],
        "planning_ms": plan["Planning Time"],
        "nodes": _plan_nodes(plan["Plan"]),
    }


def run_benchmark(
    config: Mapping[str, str | None] | None = None,
    *, max_rows: int = 10000, max_bytes: int = 16 * 1024 * 1024,
) -> dict:
    """Run only the built-in correct and deliberately incorrect SQL pairs."""
    if max_rows < 1 or max_bytes < 1:
        raise BenchmarkValidationError("Result budgets must be positive")
    with readonly_connection(config) as connection, _timeout_errors():
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        identity = connection.execute(
            "SELECT current_database(), current_user, current_setting('transaction_read_only'), "
            "current_setting('transaction_isolation'), version()"
        ).fetchone()
        original = _fetch(connection, ORIGINAL, max_rows, max_bytes)
        rewritten = _fetch(connection, REWRITTEN, max_rows, max_bytes)
        positive = _compare(original, rewritten)
        if not positive["equivalent"]:
            return {"positive": positive, "benchmark": None}
        wrong = _fetch(connection, WRONG_UPPER_BOUND, max_rows, max_bytes)
        negative = _compare(original, wrong)
        if negative["equivalent"]:
            raise BenchmarkValidationError("Wrong-upper-bound control unexpectedly matched")

        benchmark = measure_pair(connection, REWRITTEN)
        queries = {"original": ORIGINAL, "rewritten": REWRITTEN}
        return {
            "connection": dict(zip(("database", "user", "transaction_read_only",
                                     "isolation", "version"), identity)),
            "budgets": {"rows_per_query": max_rows, "serialized_bytes_per_query": max_bytes,
                        "statement_timeout_ms": 5000, "retrieval_seconds_per_query": 30},
            "sql": {**queries, "wrong_upper_bound": WRONG_UPPER_BOUND},
            "positive": positive,
            "wrong_upper_bound": {**negative, "benchmark": "skipped: results differ"},
            "benchmark": benchmark,
        }


def measure_pair(connection, candidate_sql: str, candidate_params=None, *,
                 original_sql: str = ORIGINAL, original_params=None, samples_per_query: int = 7) -> dict:
    """Measure an already validated candidate; callers must compare results first."""
    if type(samples_per_query) is not int or not 1 <= samples_per_query <= 100:
        raise ValueError('samples_per_query must be an integer in 1..100')
    # Each query is warmed once, then strictly alternate original / rewritten.
    _explain(connection, original_sql, original_params)
    _explain(connection, candidate_sql, candidate_params)
    samples = {"original": [], "rewritten": []}
    execution_order = []
    queries = {"original": original_sql, "rewritten": candidate_sql}
    for _ in range(samples_per_query):
        for name in ("original", "rewritten"):
            execution_order.append(name)
            samples[name].append(_explain(connection, queries[name], candidate_params if name == "rewritten" else original_params))
    times = {name: [sample["execution_ms"] for sample in values]
             for name, values in samples.items()}
    medians = {name: median(values) for name, values in times.items()}
    change = ((medians["rewritten"] / medians["original"] - 1) * 100
              if medians["original"] else None)
    spreads = {name: (max(values) - min(values)) / median(values)
               if median(values) else None for name, values in times.items()}
    return {
            "warmups_per_query": 1, "samples_per_query": samples_per_query,
            "execution_order": execution_order,
            "execution_ms": times, "median_ms": medians,
            "rewritten_time_change_percent": change,
            "spread_range_over_median": spreads,
            "variability_note": "较大波动：至少一组极差超过中位数的 50%"
                if any(v is not None and v > 0.5 for v in spreads.values())
                else "本次两组极差均不超过中位数的 50%",
            "samples": samples,
            "method": "Natural planner settings; warm-cache EXPLAIN server execution time, not client transfer time",
        }


def json_value(value):
    """Lossless display of mismatch values; comparison itself uses native types."""
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return {"timestamp": value.isoformat()}
    raise TypeError("Unsupported report value")
