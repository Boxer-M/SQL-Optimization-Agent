"""Sequential bounded matrix evaluation through the public optimization service."""

from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
from time import perf_counter

from sql_optimization_agent.qwen_workflow import _redact
from sql_optimization_agent.service import OptimizationService,ORDERS_DATE_SCENARIO,ORDER_ITEMS_SCENARIO

DATES=('2025-06-15','2025-06-16','2026-01-01')
SCENARIOS=(ORDERS_DATE_SCENARIO,ORDER_ITEMS_SCENARIO)
STOP_STATUSES={'authentication_error','quota_exceeded','quota_or_rate_limit','missing_config','invalid_config'}
RETAIN_STATUSES={'no_suggestion','no_rewrite'}


def matrix():
    return [{'scenario':scenario,'date':day,'repeat':repeat}
            for scenario in SCENARIOS for day in DATES for repeat in (1,2)]


def benchmark_summary(benchmark):
    if not benchmark: return None
    result={key:value for key,value in benchmark.items() if key!='samples'}
    # Deduplicate repeated plan-node metadata, retaining which sample used each plan.
    result['plan_variants']={}
    for query,samples in benchmark['samples'].items():
        variants={}
        for number,sample in enumerate(samples,1):
            key=json.dumps(sample['nodes'],sort_keys=True)
            variant=variants.setdefault(key,{'nodes':sample['nodes'],'sample_numbers':[]})
            variant['sample_numbers'].append(number)
        result['plan_variants'][query]=list(variants.values())
    return result


def record_case(case,report,elapsed_ms):
    status=report['status']
    retained=status in RETAIN_STATUSES
    validation={'status':'not_run_keep_original' if retained else 'not_completed',
                'comparison':report.get('comparison'),'counts':report.get('counts')}
    membership=report.get('membership')
    if membership:
        validation['membership']={key:value for key,value in membership.items()
                                  if key not in ('join_order_ids','candidate_order_ids')}
    if status=='completed': validation['status']='passed'
    elif status in ('result_mismatch','membership_mismatch','count_mismatch'): validation['status']='rejected'
    return {**case,'status':status,'stage':report.get('stage'),'source':report.get('source'),
            'model_id':report.get('model_id'),'model_requests':report.get('model_requests',0),
            'action':(report.get('suggestion') or {}).get('action'),
            'suggestion':report.get('suggestion'),'received_content':report.get('received_content'),
            'decision':'keep_original' if retained else 'rewrite_verified' if status=='completed' else 'not_accepted',
            'reason':report.get('reason'),'model_request_ms':report.get('model_request_ms',0.0),
            'optimization_elapsed_ms':round(elapsed_ms,3),'validation':validation,
            'candidate':report.get('candidate'),'sql':report.get('sql'),
            'parameters':report.get('parameters',(report.get('candidate') or {}).get('parameters')),
            'date_context':report.get('date_context'),
            'benchmark':None if retained else benchmark_summary(report.get('benchmark'))}


def summarize(results):
    repeated=[]
    for scenario in SCENARIOS:
        for day in DATES:
            group=[r for r in results if r['scenario']==scenario and r['date']==day]
            repeated.append({'scenario':scenario,'date':day,'completed_repeats':len(group),
                             'actions':[r['action'] for r in group],
                             'suggestion_changed':len({json.dumps(r['suggestion'],sort_keys=True) for r in group})>1,
                             'validation_changed':len({json.dumps(r['validation'],sort_keys=True) for r in group})>1})
    return {'completed_cases':len(results),'model_requests':sum(r['model_requests'] for r in results),
            'statuses':dict(Counter(r['status'] for r in results)),
            'actions':dict(Counter(r['action'] or 'unavailable' for r in results)),
            'verified_rewrites':sum(r['validation']['status']=='passed' for r in results),
            'kept_original':sum(r['decision']=='keep_original' for r in results),
            'repeated_groups':repeated}


def save(report,path,config):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(_redact(report,config),ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


def evaluate(config,output,*,live=False,service_factory=OptimizationService,progress=None):
    output=Path(output)
    if output.exists(): raise FileExistsError('Report already exists; choose a new output path')
    cases=matrix()
    report={'mode':'live' if live else 'simulated','planned_cases':cases,'maximum_model_requests':12 if live else 0,
            'requested_model':config.get('QWEN_MODEL'),'started_at_utc':datetime.now(timezone.utc).isoformat(),
            'status':'running','results':[],'summary':summarize([]),
            'timing_notes':'model_request_ms measures one HTTP request including response parsing, excluding PostgreSQL; '
                           'simulated requests are zero. SQL medians are EXPLAIN server execution times.',
            'comparison_notes':'Repeated validation_changed compares reported validation outcomes, not cross-run database snapshots.'}
    save(report,output,config)
    try:
        for case in cases:
            if live and report['summary']['model_requests']>=12:
                report.update(status='stopped',stop_reason='model_request_budget')
                break
            items=case['scenario']==ORDER_ITEMS_SCENARIO
            fixture=None if live else 'items-exists' if items else 'normal'
            service=service_factory(config,fixture=fixture)
            started=perf_counter()
            outcome=service.optimize(case['scenario'],date=case['date'],use_qwen=items)
            result=record_case(case,outcome,(perf_counter()-started)*1000)
            report['results'].append(result)
            report['summary']=summarize(report['results'])
            if result['status'] in STOP_STATUSES:
                report.update(status='stopped',stop_reason=result['status'])
            save(report,output,config)
            if progress: progress(result)
            if report['status']=='stopped': break
        else:
            report['status']='completed'
    except BaseException:
        report.update(status='interrupted',stop_reason='Evaluation interrupted; completed cases preserved')
        save(report,output,config)
        raise
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    save(report,output,config)
    return _redact(report,config)
