"""Offline EXPLAIN JSON diagnosis. No database, environment or model imports."""

import json
from math import isfinite
from pathlib import Path

from sql_optimization_agent.tools.plan_fields import plan_node_fields

MAX_BYTES=8*1024*1024


class PlanInputError(ValueError):
    def __init__(self,code,message):
        super().__init__(message)
        self.code=code


def diagnose(document):
    entries=document if isinstance(document,list) else [document]
    if not entries:
        raise PlanInputError('missing_plan_root','Missing plan root: expected a nonempty EXPLAIN document')
    plans=[]
    for index,entry in enumerate(entries):
        if not isinstance(entry,dict) or not isinstance(entry.get('Plan'),dict):
            raise PlanInputError('missing_plan_root',f'Missing Plan object in document {index}')
        observations=[];tables=set();indexes=set();node_count=0;runtime=False
        def walk(node,node_id,parent=None,depth=0):
            nonlocal node_count,runtime
            node_count+=1
            if depth>128 or node_count>10000:
                raise PlanInputError('plan_budget_exceeded','Plan exceeds depth or node budget')
            if not isinstance(node,dict) or not isinstance(node.get('Node Type'),str) or not node['Node Type']:
                raise PlanInputError('invalid_plan_node',f'Node {node_id} requires Node Type')
            for key in ('Schema','Relation Name','Index Name'):
                if key in node and not isinstance(node[key],str):
                    raise PlanInputError('invalid_plan_node',f'{key} must be a string at node {node_id}')
            for key in ('Actual Rows','Actual Loops','Plan Rows','Plan Width','Startup Cost','Total Cost'):
                if key in node and (type(node[key]) not in (int,float) or not isfinite(node[key]) or node[key]<0):
                    raise PlanInputError('invalid_plan_node',f'{key} must be a finite nonnegative number at node {node_id}')
            if 'Parallel Aware' in node and type(node['Parallel Aware']) is not bool:
                raise PlanInputError('invalid_plan_node',f'Parallel Aware must be boolean at node {node_id}')
            children=node.get('Plans',[])
            if not isinstance(children,list):
                raise PlanInputError('invalid_plan_node',f'Plans must be an array at node {node_id}')
            actual_rows='Actual Rows' in node
            actual_loops='Actual Loops' in node
            measurement='actual' if actual_rows and actual_loops else 'partial_actual' if actual_rows or actual_loops else 'estimated_only'
            runtime=runtime or actual_rows or actual_loops
            kind=node['Node Type']
            scan=('Parallel ' if node.get('Parallel Aware') and not kind.startswith('Parallel ') else '')+kind if 'Scan' in kind else None
            relation=node.get('Relation Name')
            if isinstance(relation,str): tables.add((node.get('Schema') or '',relation))
            if isinstance(node.get('Index Name'),str):indexes.add(node['Index Name'])
            if kind in ('Seq Scan','Parallel Seq Scan'):
                observations.append({'kind':'sequential_scan','node_id':node_id,'scan_method':scan,
                                     'interpretation':'Observed scan method; bottleneck status requires measurement'})
            if kind in ('Sort','Incremental Sort'):
                observations.append({'kind':'sort','node_id':node_id,
                                     'interpretation':'Observed sorting; bottleneck status requires measurement'})
            return {'node_id':node_id,'parent_id':parent,'node_type':kind,'scan_method':scan,
                    'measurement':measurement,'details':plan_node_fields(node),
                    'children':[walk(child,f'{node_id}.{number}',node_id,depth+1) for number,child in enumerate(children)]}
        root=walk(entry['Plan'],str(index))
        plans.append({'document_index':index,'runtime_data_present':runtime,
                      'measurement':'runtime_present' if runtime else 'estimated_only',
                      'planning_time_ms':entry.get('Planning Time'),'execution_time_ms':entry.get('Execution Time'),
                      'tables':[{'schema':schema or None,'name':name} for schema,name in sorted(tables)],
                      'indexes':sorted(indexes),'root':root,'observations':observations})
    return {'status':'ok','plan_count':len(plans),'plans':plans,
            'interpretation':'Observations only, not bottleneck conclusions. Actual Rows are per-loop averages; no totals are inferred.'}


def load_and_diagnose(path):
    try:
        with Path(path).open('rb') as file:data=file.read(MAX_BYTES+1)
    except OSError:
        raise PlanInputError('file_read_error','Unable to read the specified JSON file') from None
    if len(data)>MAX_BYTES:
        raise PlanInputError('input_too_large','Input exceeds 8 MiB limit')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise PlanInputError('invalid_json','Duplicate JSON object keys are not supported')
            result[key]=value
        return result
    def invalid_constant(value):raise ValueError('Non-finite JSON number')
    try:
        document=json.loads(data,object_pairs_hook=unique,parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise PlanInputError('invalid_json',f'Invalid JSON at line {error.lineno}, column {error.colno}') from None
    except (UnicodeError,ValueError,RecursionError) as error:
        if isinstance(error,PlanInputError):raise
        raise PlanInputError('invalid_json','Invalid JSON encoding, number or nesting') from None
    return diagnose(document)
