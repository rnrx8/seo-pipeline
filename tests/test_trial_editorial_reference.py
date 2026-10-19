"""Reference identity and real request handoff; no generated quality verdicts."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import content_quality as cq, step_content_audit as ca


def load_module(name, filename):
    path = Path(__file__).resolve().parents[1] / 'evaluations/product-quality' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference_module = load_module('trial_editorial_reference', 'editorial_reference.py')
replay = load_module('trial_reference_replay', 'stage6_replay.py')


class TrialEditorialReferenceTests(unittest.TestCase):
    def setUp(self):
        mode = patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER': 'tiered',
                                      'QUALITY_RESEARCH_ROUTING': 'focused'})
        mode.start()
        self.addCleanup(mode.stop)

    def fixture(self):
        text = '利用前に必要な設定を確かめ、目的に合う操作を選びます。'
        return {'status': 'user_accepted_lead_reference', 'keyword': '設定 確認',
                'lead': text, 'lead_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'positive_characteristics': ['すぐ必要な操作へ進める'],
                'use_scope': '同じKWの修正に使い、別KWの定型にしない。'}

    def read(self, value, keyword='設定 確認'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.json'
            path.write_text(json.dumps(value, ensure_ascii=False))
            return reference_module.load_accepted_reference(path, keyword)

    def test_identity_and_reason_survive_without_freezing_all_future_words(self):
        record = self.fixture()
        context = self.read(record)
        self.assertEqual(context['reference_text'], record['lead'])
        self.assertEqual(context['why_good'], record['positive_characteristics'])
        self.assertEqual(context['scope'], record['use_scope'])
        self.assertFalse(context['preserve_prefix'])
        context['why_good'].append('別の理由')
        self.assertEqual(len(record['positive_characteristics']), 1)

    def test_other_keyword_cannot_silently_receive_reference(self):
        with self.assertRaises(ValueError):
            self.read(self.fixture(), '学習 方法')

    def test_changed_text_cannot_keep_old_acceptance_hash(self):
        record = self.fixture()
        record['lead'] += '追記。'
        with self.assertRaises(ValueError):
            self.read(record)

    def test_pending_reference_cannot_be_treated_as_accepted(self):
        record = self.fixture()
        record['status'] = 'pending'
        with self.assertRaises(ValueError):
            self.read(record)

    def test_missing_editorial_reason_or_scope_is_rejected(self):
        for key, value in [('positive_characteristics', []), ('use_scope', '')]:
            with self.subTest(key=key):
                record = self.fixture()
                record[key] = value
                with self.assertRaises(ValueError):
                    self.read(record)

    def test_same_reference_reaches_actual_review_and_scoped_repair_requests(self):
        context = self.read(self.fixture())
        feedback = reference_module.reference_feedback('結びだけを調整する。', context)
        text = '# 設定を確認する\n\n' + context['reference_text'] + '\n\n## 操作\n\n設定画面を開きます。'
        decision = {'id': 'd1', 'role': 'primary', 'scope': '導入', 'depends_on': [],
                    'research_ids': ['q1'], 'question': '設定 確認',
                    'answer_requirements': ['必要な操作が分かる']}
        review, _ = replay.capture(text, decision, 'coverage', facts='', extra_requirements={
            'keyword': '設定 確認', 'custom_prompt': feedback})
        loc = {'id': 'block-0002', 'reason': '結びを整える'}
        report = {'checks': [{'key': 'coverage', 'status': 'fail', 'reason': '結びを整える',
                              'affected_blocks': [loc], 'research_ids': [], 'source_urls': [],
                              'requires_source_check': False}],
                  'editorial_findings': [{'check_key': 'coverage', 'severity': 'blocking',
                      'block_ids': ['block-0002'], 'problem': '結びを整える',
                      'reader_impact': '次の操作が曖昧', 'required_change': '結びを明確にする',
                      'stage_to_fix': 'article'}]}
        with patch('pipeline.quality_context.compact_enabled', return_value=True), \
                patch.object(ca, 'hybrid_writing_enabled', return_value=False):
            repair, packet = ca.prepare_content_repair(None, job_id='offline', text=text,
                report=report, requirements={'keyword': '設定 確認', 'custom_prompt': feedback},
                sources='[]', facts='', contract={}, issues=[])
        for request in (review, repair):
            payload = json.loads(request['messages'][0]['content'])
            self.assertEqual(payload['requirements']['custom_prompt'], feedback)
            self.assertIn(context['reference_text'], payload['requirements']['custom_prompt'])
            self.assertIn(context['why_good'][0], payload['requirements']['custom_prompt'])
        self.assertEqual(packet['target_block_ids'], ['block-0002'])

    def test_reference_change_invalidates_existing_quality_snapshot(self):
        original = {'keyword': '設定 確認', 'custom_prompt': '結びだけを調整する。'}
        referenced = {**original, 'custom_prompt': reference_module.reference_feedback(
            original['custom_prompt'], self.read(self.fixture()))}
        self.assertNotEqual(cq.snapshot('本文', '', '', {}, original),
                            cq.snapshot('本文', '', '', {}, referenced))
