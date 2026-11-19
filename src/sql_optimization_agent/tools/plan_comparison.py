"""Offline multiset comparison of observed plan features, without node pairing."""

import json

from sql_optimization_agent.tools.plan_diagnosis import load_and_diagnose, PlanInputError


def _inventory(diagnosis):
    groups = {name: {} for name in ('scans', 'tables', 'indexes', 'joins', 'sorts')}

    def add(category, feature, node):
        key = json.dumps(feature, sort_keys=True)
        entry = groups[category].setdefault(key, {'feature': feature, 'occurrences': []})
        details = node['details']
        entry['occurrences'].append({
            'node_path': node['node_id'], 'parent_path': node['parent_id'],
            'measurement': node['measurement'],
            'actual_rows': details.get('Actual Rows'),
            'actual_loops': details.get('Actual Loops'),
            'missing_runtime_fields': [field for field in ('Actual Rows', 'Actual Loops') if field not in details],
        })

    def walk(node):
        details = node['details']
        table = {'schema': details.get('Schema'), 'table': details.get('Relation Name')}
        if node['scan_method']:
            add('scans', {'method': node['scan_method'], **table, 'index': details.get('Index Name')}, node)
        if details.get('Relation Name'):
            add('tables', table, node)
        if details.get('Index Name'):
            add('indexes', {**table, 'index': details['Index Name']}, node)
        if 'Join Type' in details:
            add('joins', {'node_type': node['node_type'], 'join_type': details['Join Type']}, node)
        if node['node_type'] in ('Sort', 'Incremental Sort'):
            add('sorts', {'node_type': node['node_type'], 'sort_key': details.get('Sort Key')}, node)
        for child in node['children']:
            walk(child)

    for plan in diagnosis['plans']:
        walk(plan['root'])
    return groups


def compare_diagnoses(original, candidate):
    left, right = _inventory(original), _inventory(candidate)
    changes = {}
    for category in left:
        entries = []
        for key in sorted(left[category].keys() | right[category].keys()):
            before = left[category].get(key, {}).get('occurrences', [])
            after = right[category].get(key, {}).get('occurrences', [])
            delta = len(after) - len(before)
            entries.append({
                'feature': (left[category].get(key) or right[category][key])['feature'],
                'original_count': len(before), 'candidate_count': len(after),
                'count_delta': delta,
                'change': 'increased' if delta > 0 else 'decreased' if delta < 0 else 'unchanged_count',
                'original_occurrences': before, 'candidate_occurrences': after,
            })
        changes[category] = entries
    return {
        'status': 'ok', 'mode': 'comparison',
        'original': original, 'candidate': candidate,
        'feature_comparison': changes,
        'scope': 'Observed plan features and occurrence counts across all roots; no positional node matching.',
        'interpretation': 'Plan changes only. Equal feature counts do not imply identical trees or equivalent SQL results. Performance benefit requires the existing repeated-sampling workflow.',
    }


def load_and_compare(original_path, candidate_path):
    diagnoses = []
    for side, path in (('original', original_path), ('candidate', candidate_path)):
        try:
            diagnoses.append(load_and_diagnose(path))
        except PlanInputError as error:
            raise PlanInputError(error.code, f'{side}: {error}') from None
    return compare_diagnoses(*diagnoses)
