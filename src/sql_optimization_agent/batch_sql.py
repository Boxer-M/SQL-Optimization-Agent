"""Sequential explicit-file intake; isolate errors and retain full outcomes."""
from sql_optimization_agent.sql_intake import read_query, SQLIntakeError
from sql_optimization_agent.sql_diff import attach_sql_diff
from sql_optimization_agent.performance import attach_assessment
from sql_optimization_agent.recommendation import attach_recommendation


def error_report(status,reason,stage):
    return attach_sql_diff(attach_recommendation(attach_assessment({
        'status':status,'stage':stage,'reason':reason,'model_requests':0,'benchmark':None})))


def run_batch(paths, *, service_factory, fixture=None, use_qwen=False, repair_on_mismatch=False):
    results=[]
    for index,path in enumerate(paths,1):
        try:
            scenario,date=read_query(path)
        except SQLIntakeError as error:
            category='rejected'
            detail=error_report(error.status,str(error),'sql_intake')
        except Exception:
            category='failed'
            detail=error_report('service_error','Unable to process local SQL input','sql_intake')
        else:
            try:
                items=scenario=='orders_with_items_count'
                selected_fixture='items-exists' if items and fixture=='normal' else fixture
                detail=service_factory(fixture=selected_fixture).optimize(scenario,date=date,
                    use_qwen=items and (use_qwen or fixture=='normal'),
                    **({'repair_on_mismatch':True} if repair_on_mismatch and scenario=='orders_date_filter_2025_06_15' else {}))
                detail['sql_intake']={'source':'sql_file','dialect':'postgres','recognized_scenario':scenario,
                    'extracted_date':date,'execution':'repository_parameterized_template'}
                category='completed' if detail['status'] in ('completed','no_suggestion','no_rewrite') else 'failed'
            except Exception:
                # No raw exception text: drivers or client libraries may contain credentials.
                category='failed'
                detail=error_report('service_error','SQL file execution failed; subsequent files continued','execution')
                detail['model_requests']=None  # An unexpected exception cannot prove request counts.
        results.append({'index':index,'file':str(path),'status':category,'detail':detail})
    summary={'total':len(results),**{key:sum(row['status']==key for row in results)
                                   for key in ('completed','rejected','failed')}}
    return {'status':'completed' if not summary['rejected'] and not summary['failed'] else 'completed_with_errors',
            'summary':summary,'results':results}
