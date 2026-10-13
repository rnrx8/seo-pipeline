"""Shared acceptance policy reaches each existing gate without a pass shortcut."""
import copy
import unittest
from unittest.mock import patch
from pipeline.evidence_policy import EVIDENCE_POLICY
from pipeline.research_requirements import MATRIX_SYSTEM, PLAN_SYSTEM, matrix_policy, validate_matrix
from pipeline.research_plan_review import PLAN_REVIEW_SYSTEM
from pipeline.research_verification import COVERAGE_SYSTEM
from pipeline import tiered_research
from test_evidence_policy import fixture


class AnswerMeaningPolicyTests(unittest.TestCase):
    def test_single_policy_reaches_plan_fact_and_whole_coverage_gates(self):
        for prompt in (PLAN_SYSTEM, PLAN_REVIEW_SYSTEM, MATRIX_SYSTEM, COVERAGE_SYSTEM):
            self.assertIn(EVIDENCE_POLICY, prompt)
            self.assertEqual(prompt.count('充足は読者に伝わる意味と判断に必要な条件で評価し'), 1)
            self.assertIn('用語の定義・分類自体を検索意図やユーザーが求める場合', prompt)
            self.assertIn('根拠のない断定・誤り・結論を変える条件欠落は許可しない', prompt)

    def test_semantic_policy_does_not_automatically_override_failed_coverage(self):
        plan, pages, value = fixture()
        value.update(coverage_sufficient=False, coverage_reason='主要な判断条件が不足',
                     coverage_issues=[{'id':plan['items'][0]['id'], 'reason':'主要な判断条件が不足'}])
        self.assertTrue(validate_matrix(value, plan, pages))

    def test_policy_revision_invalidates_old_matrix_fingerprint(self):
        current = matrix_policy()
        with patch('pipeline.research_verification.COVERAGE_SYSTEM', 'previous policy'):
            self.assertNotEqual(current, matrix_policy())

    def test_tiered_gate_receives_user_scope_and_revised_question(self):
        plan, pages, value = fixture()
        plan['scope_reason'] = 'ユーザー確認済みの必要十分な回答範囲'
        plan['items'][0]['question'] = '利用自体と利用後の行為に伴う責任の違い'
        original = copy.deepcopy(value)
        captured = {}
        def request(job, step, args):
            import json
            captured.update(json.loads(args['messages'][0]['content']))
            self.assertIn(EVIDENCE_POLICY, args['system'])
            return {'coverage_sufficient':False,'coverage_reason':'未確認','coverage_issues':[]}, None
        with patch.object(tiered_research,'checked_request',side_effect=request):
            tiered_research.review_coverage('test',plan,pages,value['items'],{})
        self.assertEqual(captured['plan']['scope_reason'],plan['scope_reason'])
        self.assertEqual(captured['plan']['items'][0]['question'],plan['items'][0]['question'])
        self.assertEqual(original['items'][0]['answer'],value['items'][0]['answer'])
