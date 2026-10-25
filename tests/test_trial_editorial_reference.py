"""Reference identity and real request handoff; no generated quality verdicts."""
import copy
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

    def comparison_fixture(self):
        reference = self.read(self.fixture())
        candidate = '設定についての説明を読み、手順を比較します。'
        runs = []
        for texts in ([reference['reference_text'], candidate],
                      [candidate, reference['reference_text']]):
            cases = [{'id': key, 'lead': text}
                     for key, text in zip(('case_alpha', 'case_beta'), texts)]
            value = {'checks': [{'case_id': c['id'], 'sentence_progression': [
                {'quote': c['lead'], 'what_the_reader_receives': '操作の案内',
                 'why_the_next_sentence_is_needed': '次の操作へ進む'}]} for c in cases],
                'comparison': {'preferred_case': next(c['id'] for c in cases
                    if c['lead'] == reference['reference_text']),
                    'reason_with_short_quotes': '必要な操作へ進む説明が先にある。'}}
            runs.append({'cases': cases, 'result': value})
        return reference, candidate, runs

    def compare(self, reference, candidate, runs):
        return reference_module.checked_revision_comparison(
            '設定 確認', candidate, reference, runs)

    def test_swapped_neutral_ids_are_bound_to_text_without_overwriting_qa(self):
        reference, candidate, runs = self.comparison_fixture()
        original = copy.deepcopy(runs)
        result = self.compare(reference, candidate, runs)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(result['preferences_by_bound_text'], ['reference', 'reference'])
        self.assertFalse(result['production_qa_result_changed'])
        self.assertEqual(runs, original)

    def test_position_dependent_preference_is_inconclusive(self):
        reference, candidate, runs = self.comparison_fixture()
        runs[1]['result']['comparison']['preferred_case'] = 'case_alpha'
        self.assertEqual(self.compare(reference, candidate, runs)['status'], 'inconclusive')

    def test_candidate_preference_cannot_become_article_or_fresh_generation_acceptance(self):
        reference, candidate, runs = self.comparison_fixture()
        for run in runs:
            run['result']['comparison']['preferred_case'] = next(
                c['id'] for c in run['cases'] if c['lead'] == candidate)
        result = self.compare(reference, candidate, runs)
        self.assertEqual(result['status'], 'no_regression_detected')
        self.assertFalse(result['full_article_accepted'])
        self.assertFalse(result['fresh_generation_quality_accepted'])

    def test_single_or_repeated_position_cannot_claim_order_independence(self):
        reference, candidate, runs = self.comparison_fixture()
        for invalid in (runs[:1], [runs[0], copy.deepcopy(runs[0])]):
            with self.subTest(runs=len(invalid)), self.assertRaises(ValueError):
                self.compare(reference, candidate, invalid)

    def test_readback_cannot_quote_the_other_case_as_its_own(self):
        reference, candidate, runs = self.comparison_fixture()
        runs[0]['result']['checks'][0]['sentence_progression'][0]['quote'] = candidate
        with self.assertRaises(ValueError):
            self.compare(reference, candidate, runs)

    def test_presentation_brackets_bind_each_verbatim_fragment_without_editing_raw(self):
        reference, candidate, runs = self.comparison_fixture()
        runs[0]['result']['checks'][0]['sentence_progression'][0]['quote'] = (
            '「利用前に必要な設定を確かめ」「目的に合う操作を選びます」')
        raw = copy.deepcopy(runs)
        self.assertEqual(self.compare(reference, candidate, runs)['status'], 'needs_revision')
        self.assertEqual(runs, raw)

    def test_wrapped_fragments_do_not_allow_fabrication_other_case_or_reordering(self):
        reference, candidate, runs = self.comparison_fixture()
        for quote in ('「利用前に必要な設定を確認」',
                      '「利用前に必要な設定を確かめ」「手順を比較します」',
                      '「目的に合う操作を選びます」「利用前に必要な設定を確かめ」',
                      '「利用前に必要な設定を確かめ」という説明', '「」',
                      '「利用前に必要な…設定を確かめ」'):
            changed = copy.deepcopy(runs)
            changed[0]['result']['checks'][0]['sentence_progression'][0]['quote'] = quote
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                self.compare(reference, candidate, changed)

    def test_stale_prose_or_reference_cannot_inherit_a_comparison(self):
        reference, candidate, runs = self.comparison_fixture()
        with self.assertRaises(ValueError):
            self.compare(reference, candidate + '追記。', runs)
        reference['reference_text'] += '追記。'
        with self.assertRaises(ValueError):
            self.compare(reference, candidate, runs)

    def test_comparison_remains_scoped_to_the_accepted_keyword_and_status(self):
        reference, candidate, runs = self.comparison_fixture()
        for key, value in [('keyword', '学習 方法'), ('source_status', 'pending')]:
            changed = {**reference, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compare(changed, candidate, runs)
