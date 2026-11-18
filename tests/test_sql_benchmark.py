"""Correctness gates for multiset comparison, types and bounded execution."""

from collections import Counter
from contextlib import nullcontext
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import psycopg

from sql_optimization_agent.tools.sql_benchmark import (
    BenchmarkValidationError, QueryResult, _compare, _timeout_errors, run_benchmark,
)


class BenchmarkTests(unittest.TestCase):
    def result(self, rows, columns=None):
        return QueryResult(columns or [('amount', 1700, None, 12, 2)], Counter(rows), len(rows), 0)

    def test_order_ignored_but_duplicates_null_and_decimal_retained(self):
        rows = [(None, Decimal('1.23')), (None, Decimal('1.23')), (2, Decimal('2.34'))]
        self.assertTrue(_compare(self.result(rows), self.result(rows[::-1]))['equivalent'])
        mismatch = _compare(self.result(rows), self.result(rows[1:]))
        self.assertFalse(mismatch['equivalent'])
        self.assertEqual(mismatch['missing_from_candidate'], [{'row': [None, Decimal('1.23')], 'copies': 1}])
        changed = [(None, Decimal('1.230000001'))] + rows[1:]
        self.assertFalse(_compare(self.result(rows), self.result(changed))['equivalent'])

    def test_column_name_and_type_are_part_of_equivalence(self):
        result = self.result([(Decimal('1.23'),)])
        for columns in [[('other', 1700, None, 12, 2)], [('amount', 25, None, None, None)]]:
            self.assertFalse(_compare(result, self.result([(Decimal('1.23'),)], columns))['equivalent'])

    def test_mismatch_never_runs_performance_test(self):
        with patch('sql_optimization_agent.tools.sql_benchmark.readonly_connection', return_value=nullcontext(MagicMock())), \
             patch('sql_optimization_agent.tools.sql_benchmark._fetch', side_effect=[self.result([(1,)]), self.result([(2,)])]), \
             patch('sql_optimization_agent.tools.sql_benchmark._explain') as explain:
            report = run_benchmark({})
            self.assertIsNone(report['benchmark'])
            explain.assert_not_called()

    def test_timeout_reports_reason_without_raw_database_error(self):
        with self.assertRaisesRegex(BenchmarkValidationError, '5-second timeout'):
            with _timeout_errors():
                raise psycopg.errors.QueryCanceled('raw server text')


if __name__ == '__main__':
    unittest.main()
