"""Pure field selection shared by benchmarks and offline plan diagnosis."""

PLAN_FIELDS=(
    'Node Type','Relation Name','Schema','Alias','Index Name','Scan Direction','Join Type','Inner Unique',
    'Heap Fetches','Sort Key','Presorted Key','Sort Method','Sort Space Used','Sort Space Type',
    'Filter','Index Cond','Recheck Cond','Join Filter','Hash Cond','Merge Cond',
    'Rows Removed by Filter','Rows Removed by Join Filter','Actual Rows','Actual Loops','Parallel Aware',
    'Parent Relationship','Workers Planned','Workers Launched','Startup Cost','Total Cost','Plan Rows','Plan Width',
)


def plan_node_fields(node):
    return {key:value for key,value in node.items() if key in PLAN_FIELDS or 'Blocks' in key}
