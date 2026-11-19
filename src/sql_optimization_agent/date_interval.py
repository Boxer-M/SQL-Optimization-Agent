"""Deterministic bounded date-interval rewrite; no model calls."""
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.qwen_workflow import Candidate, _validate_candidate, _redact
from sql_optimization_agent.db import DatabaseConfigurationError, DatabaseConnectionError
from sql_optimization_agent.tools.sql_benchmark import BenchmarkValidationError

SCENARIO='orders_date_interval'
ORIGINAL='SELECT customer_id, created_at, status, amount\nFROM public.orders\nWHERE created_at::date BETWEEN %s::date AND %s::date'
CANDIDATE='SELECT customer_id, created_at, status, amount\nFROM public.orders\nWHERE created_at >= %s::timestamp without time zone\n  AND created_at < %s::timestamp without time zone'


def bounds(start,end):
    first,last=OrderDateContext.parse(start),OrderDateContext.parse(end)
    if first.target>last.target:raise ValueError('Invalid date interval: start must not follow end')
    return first,last


def run(config,start,end):
    first,last=bounds(start,end)
    report={'source':'deterministic_date_interval','scenario':SCENARIO,'model_requests':0,
            'start_date':start,'end_date':end,'stage':'result_validation','benchmark':None,
            'diagnostic_context':{'original_sql':ORIGINAL},
            'candidate':{'sql':CANDIDATE,'parameters':[first.start.isoformat(sep=' '),last.end.isoformat(sep=' ')]},
            'original_parameters':[start,end]}
    # Reuse the same snapshot, multiset, budgets and timing gate with explicit templates.
    try:
        _validate_candidate(config,first,Candidate(CANDIDATE,(first.start,last.end)),report,
                            original_sql=ORIGINAL,original_params=(first.target,last.target))
    except (DatabaseConfigurationError,DatabaseConnectionError,BenchmarkValidationError) as error:
        report.update(status='validation_error',reason=str(error))
    return _redact(report,config)
