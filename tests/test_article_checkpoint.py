import copy
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pipeline import article_checkpoint as checkpoint, step_article as writer, quality_budget as budget
from pipeline.ai import prepare_provider_request
from pipeline.content_quality import BudgetLimitError, ContentQualityError, GenerationBusyError, digest


class ArticleCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {'QUALITY_BUDGET_DIR': folder, 'QUALITY_BUDGET_MODE': 'unlimited'}))
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('offline only')))
        self.path = Path(folder) / (hashlib.sha256(b'job').hexdigest() + '.json')
        self.artifacts = {}
        self.stack.enter_context(patch.object(writer, 'upsert_artifact', side_effect=self.save))
        self.stack.enter_context(patch('pipeline.db.upsert_artifact', side_effect=self.save))
        self.stack.enter_context(patch('pipeline.db.get_optional_artifact', side_effect=lambda j, s: copy.deepcopy(self.artifacts.get(s))))
        self.messages = [{'role': 'user', 'content': '同じ調査・同じ構成'}]
        self.text = '## 比較\n\n' + '閲覧と送信で必要なプランが異なるため、利用する動作から選びます。' * 20
        self.outline = '### H2：比較'

    def save(self, **kwargs):
        self.artifacts[kwargs['step']] = copy.deepcopy(kwargs)
        return kwargs

    def part(self, number=1):
        return writer._write_complete_part(None, self.messages, 2000, self.outline, [('比較', 4, 300)], number, job_id='job')

    def record(self, number=1):
        # Run the real part validator and response persistence. Only the model
        # response is synthetic; no cache hit or delivery verdict is fabricated.
        with patch.object(writer, '_call', return_value=(self.text, 10, 20)) as send:
            self.part(number)
        request = writer._part_request(send.call_args.args[1], send.call_args.kwargs['max_tokens'])
        wire, _ = prepare_provider_request(request)
        key = digest(json.dumps(wire, ensure_ascii=False, sort_keys=True))
        self.artifacts['authoring_request_' + key] = {'content_text': json.dumps({'stage': 'article', 'request': wire})}
        call = {'provider': 'anthropic', 'stage': 'article', 'model': wire['model'],
                'provider_request_sha256': key, 'status': 'accounted', 'reserved_usd': 1, 'cost_usd': .2,
                'usage': {'stop_reason': 'end_turn', 'input_tokens': 10, 'output_tokens': 20}}
        self.path.write_text(json.dumps({'job_id': 'job', 'limit_usd': budget.LIMIT, 'calls': [call]}))
        return request

    def test_real_part_resume_keeps_response_revalidates_delivery_and_costs_zero(self):
        self.record()
        before = json.loads(self.path.read_text())
        raw = copy.deepcopy(self.artifacts['article_part_1_response_1'])
        with budget.scope('job', 'article'), patch.object(writer, '_call', side_effect=AssertionError('must not resend')):
            result = self.part()
        self.assertEqual(result, (self.text, 10, 20))
        self.assertEqual(self.artifacts['article_part_1_response_1'], raw)
        self.assertTrue(self.artifacts['article_part_1_attempt_1']['meta']['valid'])
        self.assertEqual(json.loads(self.path.read_text()), before)
        receipt = json.loads(self.artifacts['article_part_1_response_1_reuse']['content_text'])
        self.assertEqual(receipt['new_model_requests'], 0)
        self.assertFalse(receipt['quality_accepted'])

    def test_legacy_response_without_new_hash_fields_is_bound_to_saved_input_and_usage(self):
        request = self.record()
        self.artifacts['article_part_1_response_1']['meta'].pop('provider_request_sha256')
        self.artifacts['article_part_1_response_1']['meta'].pop('response_sha256')
        with budget.scope('job', 'article'):
            self.assertEqual(checkpoint.saved('job', 1, 1, request)['content_text'], self.text)

    def test_mismatched_input_usage_or_body_never_falls_through_to_resend(self):
        for kind in ('input', 'usage', 'body', 'missing_response', 'missing_request'):
            self.artifacts.clear()
            request = self.record()
            if kind == 'input': request['messages'][0]['content'] += '変わった条件'
            if kind == 'usage': self.artifacts['article_part_1_response_1']['meta']['output_tokens'] += 1
            if kind == 'body': self.artifacts['article_part_1_response_1']['content_text'] += '別の文章'
            if kind == 'missing_response': self.artifacts.pop('article_part_1_response_1')
            if kind == 'missing_request':
                self.artifacts = {k: v for k, v in self.artifacts.items() if not k.startswith('authoring_request_')}
            with self.subTest(kind=kind), budget.scope('job', 'article'), self.assertRaises(ContentQualityError):
                checkpoint.saved('job', 1, 1, request)

    def test_pending_and_unknown_requests_never_resend_or_claim_delivery(self):
        request = self.record()
        for status in ('pending', 'unknown_cost_reserved'):
            value = json.loads(self.path.read_text()); value['calls'][0]['status'] = status
            self.path.write_text(json.dumps(value))
            with self.subTest(status=status), budget.scope('job', 'article'), self.assertRaises(GenerationBusyError):
                checkpoint.saved('job', 1, 1, request)

    def test_next_unsent_part_remains_eligible_without_resetting_previous_spend(self):
        self.record()
        before = json.loads(self.path.read_text())
        with budget.scope('job', 'article'), patch.object(writer, '_call', return_value=(self.text, 11, 21)) as send:
            self.part(1)
            self.messages = [{'role': 'user', 'content': '後半の章を執筆する依頼'}]
            self.part(2)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text()), before)

    def test_budget_error_is_a_funding_requirement_not_article_failure(self):
        from pipeline.autofix import classify_error
        error = BudgetLimitError(1.1, .67)
        report = classify_error(error)
        self.assertEqual(report['type'], 'budget')
        self.assertEqual(report['required_reservation_usd'], 1.1)
        self.assertFalse(report['request_sent'])
        self.assertNotIn('本人が指定', str(error))
