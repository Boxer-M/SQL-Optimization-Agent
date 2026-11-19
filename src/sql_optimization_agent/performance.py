"""Conservative interval assessment of verified, completed benchmark samples."""

from math import isfinite
from statistics import median


def assess_samples(original, candidate):
    for values in (original,candidate):
        if not isinstance(values,(list,tuple)) or not values:
            raise ValueError('Completed timing samples are required')
        if any(type(value) not in (int,float) or not isfinite(value) or value<0 for value in values):
            raise ValueError('Timing samples must be finite nonnegative numbers')
    if len(original)!=len(candidate):
        raise ValueError('Both timing groups must be complete')
    baseline=median(original)
    proposed=median(candidate)
    verdict=('improved' if max(candidate)<min(original) else
             'regressed' if min(candidate)>max(original) else 'inconclusive')
    return {'verdict':verdict,'rule':'strict_sample_interval_separation',
            'original_range_ms':{'min':min(original),'max':max(original)},
            'candidate_range_ms':{'min':min(candidate),'max':max(candidate)},
            'median_ms':{'original':baseline,'rewritten':proposed},
            'median_difference_ms':round(proposed-baseline,9),
            'median_change_percent':((proposed/baseline-1)*100) if baseline else None,
            'sample_counts':{'original':len(original),'rewritten':len(candidate)},
            'scope':'This sampling run only; not a general performance guarantee'}


def attach_assessment(report):
    result={**report,'performance_assessment':None}
    if report.get('status')!='completed':
        result['performance_assessment_skipped']='result_not_verified_or_original_retained'
        return result
    comparison=report.get('comparison')
    membership=report.get('membership')
    if membership is not None:
        counts=report.get('counts') or {}
        ids=membership.get('comparison') or {}
        verified=(ids.get('equivalent') is True and membership.get('join_minus_candidate_count')==0
                  and membership.get('candidate_minus_join_count')==0
                  and type(counts.get('join_count_distinct')) is int
                  and counts.get('join_count_distinct')==counts.get('candidate_count')
                  and counts.get('join_count_distinct')==ids.get('original_rows')==ids.get('candidate_rows'))
    else:
        verified=isinstance(comparison,dict) and comparison.get('equivalent') is True
    if not verified:
        result['performance_assessment_skipped']='missing_or_inconsistent_validation_evidence'
        return result
    benchmark=report.get('benchmark')
    times=benchmark.get('execution_ms',{}) if isinstance(benchmark,dict) else {}
    try:
        original,candidate=times.get('original'),times.get('rewritten')
        assessment=assess_samples(original,candidate)
        expected=benchmark.get('samples_per_query',len(original))
        if len(original)!=expected or len(candidate)!=expected: raise ValueError
    except (ValueError,TypeError,AttributeError):
        result['performance_assessment_skipped']='timing_incomplete_or_invalid'
        return result
    result.pop('performance_assessment_skipped',None)
    result['performance_assessment']=assessment
    return result
