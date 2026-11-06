import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from pipeline.openai_review import create_review_response
from pipeline.content_quality import ContentQualityError


class ReviewHttpDiagnosticsTests(unittest.TestCase):
    def test_terminal_rejection_keeps_safe_reason_without_retry_or_erasing_cost_hold(self):
        value = {'calls': [{}]}
        @contextmanager
        def ledger(): yield value
        key = 'sk-private-fixture-key'
        response = Mock(status_code=400, headers={'x-request-id': 'req_fixture'})
        response.json.return_value = {'error': {'type': 'invalid_request_error', 'code': 'invalid_json_schema',
            'param': 'text.format.schema', 'message': 'Invalid newline literal; diagnostic ' + key}}
        with patch.dict(os.environ, {'OPENAI_API_KEY': key}), \
             patch('pipeline.openai_review.requests.post', return_value=response) as send, \
             patch('pipeline.quality_budget.reserve', return_value=0), \
             patch('pipeline.quality_budget.settle') as settle, \
             patch('pipeline.quality_budget.ledger', side_effect=ledger):
            with self.assertRaisesRegex(ContentQualityError, 'HTTP 400'):
                create_review_response(model='gpt-6.1-sol', max_tokens=100, system='QA',
                    messages=[{'role': 'user', 'content': 'sample'}],
                    output_config={'format': {'schema': {'type': 'object'}}})
        self.assertEqual(send.call_count, 1)
        settle.assert_called_once_with(0, None)
        diagnostics = value['calls'][0]['diagnostics']
        self.assertEqual(diagnostics['provider_error']['code'], 'invalid_json_schema')
        self.assertEqual(diagnostics['provider_error']['param'], 'text.format.schema')
        self.assertNotIn(key, str(diagnostics))
        self.assertIn('[redacted]', diagnostics['provider_error']['message'])
        response.close.assert_called_once()
