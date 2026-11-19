"""Three independent fixed-initial / model-repair cases; live mode is explicit."""

from datetime import datetime, timezone
import json
from pathlib import Path

from sql_optimization_agent.batch_evaluation import save, STOP_STATUSES
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation
from sql_optimization_agent.qwen_workflow import run_workflow, _redact
from sql_optimization_agent.tools.sql_benchmark import json_value


def summarize(results):
    return {'executed_cases': len(results),
            'real_model_requests': sum(row.get('model_requests', 0) for row in results),
            'passed_cases': sum(row['status'] == 'completed' and
                                (row.get('comparison') or {}).get('equivalent') is True and
                                row.get('benchmark') is not None for row in results),
            'timed_cases': sum(row.get('benchmark') is not None for row in results)}


def evaluate(config, output, *, live=False, progress=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Report already exists')
    # The evaluation deliberately fixes the date and model; the API key/base URL stay local.
    config = {**config, 'QWEN_MODEL': 'qwen3.7-plus'}
    report = {'mode': 'fixed_initial_then_live_qwen' if live else 'fixed_initial_then_simulated_repair',
              'target_date': '2025-06-15', 'requested_model': config['QWEN_MODEL'],
              'planned_cases': 3, 'maximum_real_requests': 3 if live else 0,
              'case_request_limit': 1 if live else 0,
              'attempt_labels': ['首轮固定候选', '第二轮真实千问' if live else '第二轮模拟响应'],
              'started_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'results': [], 'summary': summarize([])}
    save(report, output, config)
    try:
        for repeat in range(1, 4):
            outcome = run_workflow(config, date_context=OrderDateContext.parse('2025-06-15'),
                                   fixture=None if live else 'normal', repair_on_mismatch=True,
                                   fixed_initial_wrong_upper=True)
            outcome = attach_recommendation(attach_assessment(outcome))
            outcome.update(case=repeat, attempt_labels=report['attempt_labels'],
                           was_timed=outcome.get('benchmark') is not None)
            outcome = json.loads(json.dumps(outcome, ensure_ascii=False, default=json_value))
            report['results'].append(outcome)
            report['summary'] = summarize(report['results'])
            if outcome['status'] in STOP_STATUSES | {'evaluation_precondition_failed'}:
                report.update(status='stopped', stop_reason=outcome['status'])
            save(report, output, config)
            if progress:
                progress(outcome)
            if report['status'] == 'stopped':
                break
        else:
            report['status'] = 'completed'
    except BaseException:
        report.update(status='interrupted', stop_reason='Evaluation interrupted; completed cases preserved')
        save(report, output, config)
        raise
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save(report, output, config)
    return _redact(report, config)
