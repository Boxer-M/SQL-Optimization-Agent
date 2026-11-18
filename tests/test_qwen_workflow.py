"""Controlled model contracts, single-request transport and execution gates."""

import json
import unittest
from unittest.mock import patch

import httpx

from sql_optimization_agent.qwen_client import ModelReply, QwenFailure, request_suggestion
from sql_optimization_agent.qwen_workflow import FIXTURES as FIXTURE_NAMES, fixture_content, parse_suggestion, run_workflow
from sql_optimization_agent.order_date import OrderDateContext
from sql_optimization_agent.service import DEFAULT_ORDER_DATE

DATE_CONTEXT = OrderDateContext.parse(DEFAULT_ORDER_DATE)
FIXTURES = {name: fixture_content(name, DATE_CONTEXT) for name in FIXTURE_NAMES}


CONFIG = {"QWEN_API_KEY": "unit-test-secret-not-real", "QWEN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
          "QWEN_MODEL": "unit-test-model"}


class QwenTests(unittest.TestCase):
    def test_candidate_is_template_and_bound_values(self):
        value, candidate = parse_suggestion(FIXTURES['normal'], DATE_CONTEXT)
        self.assertEqual(candidate.sql.count('%s'), 2)
        self.assertNotIn(value['start'], candidate.sql)
        self.assertEqual(candidate.params[1].isoformat(), '2025-06-16T00:00:00')
        _, wrong = parse_suggestion(FIXTURES['wrong-upper'], DATE_CONTEXT)
        self.assertIn('<= %s', wrong.sql)

    def test_rejects_extra_sql_wrong_types_ranges_and_duplicate_keys(self):
        good = json.loads(FIXTURES['normal'])
        cases = [FIXTURES['malformed'], 'not JSON', '[]',
                 json.dumps({**good, 'sql': 'SELECT 1'}),
                 json.dumps({**good, 'action': 'execute_sql'}),
                 json.dumps({**good, 'start_inclusive': 1}),
                 json.dumps({**good, 'end': '2025-06-17 00:00:00'}),
                 '{"action":"date_range","action":"no_suggestion","reason":"x"}']
        for content in cases:
            with self.subTest(content=content), self.assertRaises(QwenFailure):
                parse_suggestion(content, DATE_CONTEXT)

    def test_no_suggestion_has_no_candidate(self):
        self.assertIsNone(parse_suggestion(FIXTURES['no-suggestion'], DATE_CONTEXT)[1])

    def test_one_request_json_contract_and_no_secrets_in_prompt(self):
        requests = []
        def handle(request):
            requests.append(request)
            payload = json.loads(request.content)
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertNotIn(CONFIG['QWEN_API_KEY'], json.dumps(payload))
            return httpx.Response(200, json={'model': 'returned-model', 'choices': [
                {'finish_reason': 'stop', 'message': {'content': FIXTURES['normal']}}]})
        reply = request_suggestion({'original_sql': 'fixed input'}, CONFIG, transport=httpx.MockTransport(handle))
        self.assertEqual(len(requests), 1)
        self.assertEqual(reply.model, 'returned-model')

    def test_http_errors_and_timeouts_are_sanitized_and_never_retried(self):
        for status in (429, 401, 403, 500):
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(status, text=CONFIG['QWEN_API_KEY'])
            with self.assertRaises(QwenFailure) as caught:
                request_suggestion({}, CONFIG, transport=httpx.MockTransport(handle))
            self.assertEqual(len(calls), 1)
            self.assertNotIn(CONFIG['QWEN_API_KEY'], str(caught.exception))
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout('secret raw error')
        with self.assertRaises(QwenFailure) as caught:
            request_suggestion({}, CONFIG, transport=httpx.MockTransport(timeout))
        self.assertEqual(caught.exception.status, 'model_timeout')
        self.assertEqual(len(calls), 1)

    def test_missing_configuration_and_bad_urls_fail_before_request(self):
        for config in ({}, {**CONFIG, 'QWEN_API_KEY': ''},
                       {**CONFIG, 'QWEN_BASE_URL': 'https://dashscope.aliyuncs.com:bad/compatible-mode/v1'},
                       {**CONFIG, 'QWEN_BASE_URL': 'https://example.com/compatible-mode/v1'}):
            with self.assertRaises(QwenFailure):
                request_suggestion({}, config)

    def test_quota_and_invalid_envelopes_have_clear_status(self):
        for response, expected in [
            (httpx.Response(400, json={'error': {'code': 'AllocationQuota.FreeTierOnly'}}), 'quota_exceeded'),
            (httpx.Response(200, json={'choices': []}), 'invalid_response'),
            (httpx.Response(200, json={'model': 'm', 'choices': [
                {'finish_reason': 'length', 'message': {'content': '{}'}}]}), 'invalid_response'),
        ]:
            with self.subTest(expected=expected), self.assertRaises(QwenFailure) as caught:
                request_suggestion({}, CONFIG, transport=httpx.MockTransport(lambda request: response))
            self.assertEqual(caught.exception.status, expected)

    def test_invalid_suggestion_never_reaches_database_validation(self):
        with patch('sql_optimization_agent.qwen_workflow.diagnostic_context', return_value={}), \
             patch('sql_optimization_agent.qwen_workflow._fetch') as fetch, \
             patch('sql_optimization_agent.qwen_workflow.measure_pair') as measure:
            report = run_workflow(CONFIG, date_context=DATE_CONTEXT, fixture='malformed')
            self.assertEqual(report['status'], 'invalid_suggestion')
            self.assertIsNone(report['benchmark'])
            fetch.assert_not_called()
            measure.assert_not_called()

    def test_received_text_is_redacted_in_reports(self):
        with patch('sql_optimization_agent.qwen_workflow.diagnostic_context', return_value={}), \
             patch('sql_optimization_agent.qwen_workflow.request_suggestion',
                   return_value=ModelReply('unit-model', CONFIG['QWEN_API_KEY'])):
            report = run_workflow(CONFIG, date_context=DATE_CONTEXT)
            self.assertNotIn(CONFIG['QWEN_API_KEY'], json.dumps(report))


if __name__ == '__main__':
    unittest.main()
