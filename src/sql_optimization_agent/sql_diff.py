"""Stable, offline template diff for verified candidates only."""

from difflib import unified_diff
from sql_optimization_agent.performance import validation_passed


def attach_sql_diff(report):
    result={**report,'sql_diff':None}
    if report.get('status')!='completed' or not validation_passed(report):
        return result
    original=(report.get('sql') or {}).get('join_count_distinct')
    if original is None:
        original=(report.get('diagnostic_context') or {}).get('original_sql')
    candidate=(report.get('candidate') or {}).get('sql')
    if not all(isinstance(value,str) and value.strip() for value in (original,candidate)):
        return result
    def lines(sql):
        return [line.rstrip()+'\n' for line in sql.strip().splitlines()]
    result['sql_diff']=''.join(unified_diff(lines(original),lines(candidate),
                                          fromfile='original.sql',tofile='candidate.sql',lineterm='\n'))
    return result
