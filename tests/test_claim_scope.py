import unittest
from pipeline.claim_scope import conditional_facts, scope_issues, scope_instructions

FACTS='## サービスA\n> 女性は基本機能が無料。男性はメッセージ送受信が有料。\n> 1ヶ月プランは税込980円。 [confirmed]'

class ClaimScopeTests(unittest.TestCase):
    def test_conditions_and_values_remain_attached_to_literal_evidence(self):
        row=conditional_facts(FACTS)[0]
        self.assertEqual(row['conditions']['audience'],['女性','男性'])
        self.assertIn('税込',row['conditions']['tax'])
        self.assertIn('1ヶ月',row['conditions']['term'])
        self.assertIn('980円',row['values'])
        self.assertIn('女性は基本機能が無料',row['statement'])
        self.assertIn('全組合せに値が適用される意味ではない',scope_instructions(FACTS))

    def test_unqualified_free_conclusion_cannot_pass_with_female_free_evidence(self):
        text='# 無料サービス\n## 無料の範囲\n完全無料で利用できるサービスは基本的に存在しません。\n## まとめ\n完全無料のサービスはありません。'
        self.assertEqual(len(scope_issues(text,FACTS)),2)

    def test_explicit_audience_or_scoped_heading_is_valid(self):
        for text in ['## 無料の範囲\n男性が完全無料で使い続けられるサービスはありません。','## 男性が使えるサービス\n完全無料のサービスはありません。']:
            self.assertEqual(scope_issues(text,FACTS),[])

    def test_heading_scope_does_not_leak_to_next_section(self):
        text='## 男性の場合\n完全無料は存在しません。\n## まとめ\n完全無料は存在しません。'
        self.assertEqual(len(scope_issues(text,FACTS)),1)

    def test_questions_negation_and_opposite_gender_prices_are_not_counterexamples(self):
        for text in ['「完全無料は存在しません」と思っていませんか。','完全無料は存在しないとは限りません。']:
            self.assertEqual(scope_issues(text,FACTS),[])
        for facts in ['> 女性は有料。男性は無料。 [confirmed]','> 女性は無料ではありません。 [confirmed]']:
            self.assertEqual(scope_issues('完全無料は存在しません。',facts),[])
