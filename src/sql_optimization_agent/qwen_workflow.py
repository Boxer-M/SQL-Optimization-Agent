"""Controlled orders-only diagnosis → model advice → validation → measurement."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json

import psycopg

from sql_optimization_agent.db import (
    DatabaseConfigurationError, DatabaseConnectionError, readonly_connection,
)
from sql_optimization_agent.qwen_client import ModelReply, QwenFailure, request_suggestion
from sql_optimization_agent.order_date import ORDER_DATE_SQL, OrderDateContext
from sql_optimization_agent.tools.schema_inspector import inspect_table, TableNotFoundError
from sql_optimization_agent.tools.sql_benchmark import (
    BenchmarkValidationError, _compare, _fetch, _timeout_errors, measure_pair,
)


@dataclass(frozen=True)
class Candidate:
    sql: str
    params: tuple[datetime, datetime]


def parse_suggestion(content: str, date_context: OrderDateContext) -> tuple[dict, Candidate | None]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        value = json.loads(content, object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError):
        raise QwenFailure("invalid_suggestion", "Suggestion must be one JSON object without duplicate keys") from None
    if not isinstance(value, dict):
        raise QwenFailure("invalid_suggestion", "Suggestion must be a JSON object")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise QwenFailure("invalid_suggestion", "Suggestion requires a nonempty reason of at most 1000 characters")
    if value.get("action") == "no_suggestion" and set(value) == {"action", "reason"}:
        return value, None
    keys = {"action", "start", "end", "start_inclusive", "end_inclusive", "reason"}
    if set(value) != keys or value.get("action") != "date_range":
        raise QwenFailure("invalid_suggestion", "Only date_range fields or no_suggestion are supported; SQL is not accepted")
    if (value["start"] != date_context.start.isoformat(sep=" ") or value["end"] != date_context.end.isoformat(sep=" ")
            or type(value["start_inclusive"]) is not bool or type(value["end_inclusive"]) is not bool):
        raise QwenFailure("invalid_suggestion", "Unsupported date range or non-boolean boundary flags")
    start, end = date_context.start, date_context.end
    # Only these operators are selected by application code; model text is never SQL.
    lower = ">=" if value["start_inclusive"] else ">"
    upper = "<=" if value["end_inclusive"] else "<"
    sql = ("SELECT customer_id, created_at, status, amount\nFROM public.orders\n"
           f"WHERE created_at {lower} %s::timestamp without time zone\n"
           f"  AND created_at {upper} %s::timestamp without time zone")
    return value, Candidate(sql, (start, end))


def _estimated_nodes(node):
    keep = {"Node Type", "Relation Name", "Index Name", "Filter", "Index Cond",
            "Recheck Cond", "Plan Rows", "Plan Width", "Startup Cost", "Total Cost", "Parallel Aware"}
    result = [{key: value for key, value in node.items() if key in keep}]
    for child in node.get("Plans", []):
        result.extend(_estimated_nodes(child))
    return result


def diagnostic_context(config: Mapping, date_context: OrderDateContext) -> dict:
    schema = inspect_table("public", "orders", config)
    expected = {"customer_id": "integer", "created_at": "timestamp without time zone",
                "status": "text", "amount": "numeric(12,2)"}
    columns = {c["name"]: c["data_type"] for c in schema["columns"]}
    if any(columns.get(name) != data_type for name, data_type in expected.items()):
        raise QwenFailure("unsupported_schema", "Orders schema does not match the supported date-filter scenario")
    with readonly_connection(config) as connection, _timeout_errors():
        connection.execute("SET LOCAL statement_timeout = '5s'")
        plan = connection.execute("EXPLAIN (FORMAT JSON) " + ORDER_DATE_SQL, date_context.original_params).fetchone()[0][0]
    return {"scenario": "orders_date_filter_2025_06_15", "original_sql": ORDER_DATE_SQL,
            "original_parameters": [date_context.target.isoformat()], "date_context": date_context.as_dict(),
            "table": schema, "estimated_plan": _estimated_nodes(plan["Plan"])}


def _redact(value, config):
    if isinstance(value, str):
        for key in ("QWEN_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_READER_PASSWORD"):
            secret = config.get(key)
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: _redact(item, config) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, config) for item in value]
    return value


def _validate_candidate(config, date_context, candidate, report, *, allow_benchmark=True):
    """Each attempt validates and measures in its own bounded read-only snapshot."""
    report["stage"] = "result_validation"
    with readonly_connection(config) as connection, _timeout_errors():
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
        identity = connection.execute(
            "SELECT current_database(), current_user, current_setting('transaction_read_only'), "
            "current_setting('transaction_isolation'), version()"
        ).fetchone()
        report["connection"] = dict(zip(("database", "user", "read_only", "isolation", "version"), identity))
        original = _fetch(connection, ORDER_DATE_SQL, 10000, 16 * 1024 * 1024, date_context.original_params)
        proposed = _fetch(connection, candidate.sql, 10000, 16 * 1024 * 1024, candidate.params)
        report["comparison"] = _compare(original, proposed)
        # Feedback never includes row values, metadata names, or truncated row samples.
        report["difference_summary"] = {
            "original_rows": original.count, "candidate_rows": proposed.count,
            "missing_row_copies": sum((original.rows - proposed.rows).values()),
            "extra_row_copies": sum((proposed.rows - original.rows).values()),
            "columns_equal": report["comparison"]["columns_equal"],
            "equivalent": report["comparison"]["equivalent"],
        }
        if report["comparison"]["equivalent"] and not allow_benchmark:
            report.update(status="validation_only", reason="Fixed initial candidate validation only; no timing")
        elif report["comparison"]["equivalent"]:
            report["stage"] = "benchmark"
            report["benchmark"] = measure_pair(connection, candidate.sql, candidate.params,
                                               original_sql=ORDER_DATE_SQL, original_params=date_context.original_params)
            report.update(status="completed", stage="complete", reason="在本次测试数据上结果一致")
        else:
            report.update(status="result_mismatch", reason="Full results differ; candidate benchmark skipped")


def run_workflow(config: Mapping, *, date_context: OrderDateContext, fixture: str | None = None,
                 repair_on_mismatch: bool = False, fixed_initial_wrong_upper: bool = False) -> dict:
    report = {"source": ("fixed_initial_then_live_qwen" if fixed_initial_wrong_upper and not fixture else
                         "controlled_model_response" if fixture else "live_qwen"),
              "fixture": fixture, "requested_model": config.get("QWEN_MODEL"),
              "measured_at_utc": datetime.now(timezone.utc).isoformat(), "model_requests": 0,
              "benchmark": None, "model_request_ms": 0.0, "stage": "diagnosis", "target_date": date_context.target.isoformat(),
              "date_context": date_context.as_dict(), "repair_on_mismatch": repair_on_mismatch,
              "max_model_requests": (int(repair_on_mismatch) if fixed_initial_wrong_upper else
                                     2 if repair_on_mismatch else 1), "attempts": []}
    attempt = None
    try:
        context = diagnostic_context(config, date_context)
        report["diagnostic_context"] = context
        for number in range(1, 3 if repair_on_mismatch else 2):
            attempt = {"attempt": number, "kind": "initial" if number == 1 else "repair",
                       "stage": "model_request", "benchmark": None, "model_requests": 0}
            report["attempts"].append(attempt)
            for key in ("candidate", "comparison", "difference_summary", "connection",
                        "model_id", "received_content", "suggestion", "reason", "status"):
                report.pop(key, None)
            report["benchmark"] = None
            report["stage"] = "model_request"
            fixed_initial = fixed_initial_wrong_upper and number == 1
            attempt["source"] = "fixed_wrong_upper" if fixed_initial else "controlled_model_response" if fixture else "live_qwen"
            if fixture or fixed_initial:
                if fixture is not None and fixture not in FIXTURES:
                    raise QwenFailure("invalid_fixture", "Unknown controlled response fixture")
                name = "wrong-upper" if fixed_initial else fixture
                if not fixed_initial and fixture in ("repair-success", "repair-failure"):
                    name = "normal" if number == 2 and fixture == "repair-success" else "wrong-upper"
                reply = ModelReply("fixed-candidate-not-a-model" if fixed_initial else "controlled-fixture-not-a-real-model",
                                   fixture_content(name, date_context))
            else:
                # One POST per call; only the explicit mismatch gate can make a second call.
                try:
                    reply = request_suggestion(context, config)
                except QwenFailure as error:
                    attempt["model_requests"] = 0 if error.status in ("missing_config", "invalid_config") else 1
                    raise
                attempt["model_requests"] = 1
            attempt.update(model_id=reply.model, received_content=reply.content,
                           model_request_ms=reply.request_elapsed_ms or 0.0, stage="suggestion_validation")
            report["stage"] = "suggestion_validation"
            suggestion, candidate = parse_suggestion(reply.content, date_context)
            attempt["suggestion"] = suggestion
            if candidate is None:
                attempt.update(status="no_suggestion", stage="complete", reason="Model returned no suggestion")
            else:
                attempt["candidate"] = {"sql": candidate.sql,
                                        "parameters": [value.isoformat(sep=" ") for value in candidate.params]}
                _validate_candidate(config, date_context, candidate, attempt,
                                    **({"allow_benchmark": False} if fixed_initial else {}))
            # Root fields always describe the latest attempt; historical evidence stays in attempts.
            for key in ("candidate", "comparison", "difference_summary", "connection"):
                report.pop(key, None)
            report.update(attempt)
            if fixed_initial:
                summary = attempt.get("difference_summary") or {}
                if (attempt["status"] != "result_mismatch" or summary.get("original_rows") != 825
                        or summary.get("candidate_rows") != 826):
                    attempt.update(status="evaluation_precondition_failed",
                                   reason="Fixed initial candidate must produce a verified 825/826 mismatch; repair request skipped")
                    report.update(attempt)
                    break
            if attempt["status"] != "result_mismatch" or not repair_on_mismatch or number == 2:
                break
            feedback = {"target_date": date_context.target.isoformat(),
                        "candidate_boundaries": {key: suggestion[key] for key in
                                                 ("start", "end", "start_inclusive", "end_inclusive")},
                        "difference_summary": attempt["difference_summary"],
                        "instruction": "Repair the date boundaries using the same structured contract, or return no_suggestion."}
            report["repair_feedback"] = feedback
            # The first transaction is closed before sending aggregate-only feedback.
            context = {**context, "repair_feedback": feedback}
    except QwenFailure as error:
        if attempt is not None:
            attempt.update(status=error.status, reason=str(error))
            if error.request_elapsed_ms is not None:
                attempt["model_request_ms"] = error.request_elapsed_ms
            for key in ("candidate", "comparison", "difference_summary", "connection"):
                report.pop(key, None)
            report.update(attempt)
        report.update(status=error.status, reason=str(error))
    except (DatabaseConfigurationError, DatabaseConnectionError, BenchmarkValidationError, TableNotFoundError) as error:
        if attempt is not None:
            attempt.update(status="validation_error", reason=str(error))
            report.update(attempt)
        report.update(status="validation_error", reason=str(error))
    # Attempt-local counters must not replace workflow totals during report.update().
    report["model_requests"] = sum(item["model_requests"] for item in report["attempts"])
    report["model_request_ms"] = sum(item.get("model_request_ms", 0.0) for item in report["attempts"])
    report.pop("attempt", None)
    report.pop("kind", None)
    return _redact(report, config)


FIXTURES = ("normal", "malformed", "wrong-upper", "no-suggestion", "repair-success", "repair-failure")


def fixture_content(name: str, date_context: OrderDateContext) -> str:
    if name in ("repair-success", "repair-failure"):
        name = "wrong-upper"
    if name == "malformed":
        return '{"action": "date_range", "sql": "not executable model text"}'
    if name == "no-suggestion":
        return json.dumps({"action": "no_suggestion", "reason": "Controlled no-suggestion path."})
    if name not in ("normal", "wrong-upper"):
        raise QwenFailure("invalid_fixture", "Unknown controlled response fixture")
    return json.dumps({"action": "date_range", "start": date_context.start.isoformat(sep=" "),
                       "end": date_context.end.isoformat(sep=" "), "start_inclusive": True,
                       "end_inclusive": name == "wrong-upper",
                       "reason": "Controlled date-window suggestion; wrong-upper deliberately includes next midnight."
                       if name == "wrong-upper" else "Use the timestamp B-tree with inclusive start and exclusive end."})
