"""Membership gates must catch equal-count but different-order results."""

from collections import Counter
from contextlib import nullcontext
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from sql_optimization_agent.tools.sql_benchmark import QueryResult, _plan_nodes


@pytest.fixture
def benchmark_module():
    spec = importlib.util.spec_from_file_location('items_benchmark',Path(__file__).resolve().parents[1]/'scripts/benchmark_order_items.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_equal_counts_with_swapped_id_stop_before_count_and_timing(benchmark_module,monkeypatch):
    connection = MagicMock()
    connection.execute.return_value.fetchone.side_effect = [
        ('sql_optimization','sql_optimization_reader','on','repeatable read','test'),
        (400008,), (825,274,275,276,1103)]
    monkeypatch.setattr(benchmark_module,'readonly_connection',lambda _:nullcontext(connection))
    def result(ids):
        return QueryResult([('order_id',20)],Counter((i,) for i in ids),len(ids),100)
    monkeypatch.setattr(benchmark_module,'_fetch',Mock(side_effect=[result([1,2]),result([1,3])]))
    measure = Mock()
    monkeypatch.setattr(benchmark_module,'measure_pair',measure)
    report = benchmark_module.run({})
    assert report['status']=='membership_mismatch'
    assert report['membership']['join_minus_exists']==[2]
    assert report['membership']['exists_minus_join']==[3]
    assert report['benchmark'] is None
    assert 'counts' not in report
    measure.assert_not_called()


def test_plan_summary_preserves_semi_join_and_heap_fetches():
    nodes = _plan_nodes({'Node Type':'Nested Loop','Join Type':'Semi','Plans':[
        {'Node Type':'Index Only Scan','Heap Fetches':4}]})
    assert nodes[0]['Join Type']=='Semi'
    assert nodes[1]['Heap Fetches']==4
