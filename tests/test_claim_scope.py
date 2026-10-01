import unittest
from pipeline.claim_scope import conditional_facts, scope_issues, scope_instructions

FACTS='## サービスA\n> 女性は基本機能が無料。男性はメッセージ送受信が有料。\n> 1ヶ月プランは税込980円。 [confirmed]'

class ClaimScopeTests(unittest.TestCase):
    def test_metric_definition_survives_redundancy_deletion(self):
        facts = 'アクティブユーザー率は約75%（2026年2月にアクセスしたユーザーの内、当月後半にもアクティブだった割合）。'
        table = '| アクティブ率 | 約75% | 公式非公表 |'
        self.assertEqual(scope_issues(table, facts)[0]['key'], 'missing_metric_cohort')
        self.assertEqual(scope_issues(table + '\n\n' + facts, facts), [])
        self.assertEqual(scope_issues('アクティブユーザー率は約75%です。これは2026年2月にアクセスしたユーザーのうち当月後半にもアクティブだった割合です。', facts), [])
        self.assertTrue(scope_issues(table + '\n\n' + facts.replace('2026年2月', '2026年3月'), facts))
        self.assertTrue(scope_issues(table + '\n\nアクティブ率約75%（2026年2月時点）。', facts))
        self.assertEqual(scope_issues('会員数は非公表です。', facts), [])
        self.assertEqual(scope_issues(table, 'アクティブ率は約75%です。'), [])

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
        for facts in ['> 女性は有料。男性は無料。 [confirmed]','> 女性は無料ではありません。 [confirmed]', '> 女性は無料ではなく有料です。 [confirmed]']:
            self.assertEqual(scope_issues('完全無料は存在しません。',facts),[])

    def test_gender_comparison_heading_is_not_a_male_only_condition(self):
        text='# 男性・女性の無料範囲\n## まとめ\n完全無料のサービスは存在しません。'
        self.assertEqual(len(scope_issues(text,FACTS)),1)

    def test_final_gate_rejects_scope_error_even_with_forged_pass_verdict(self):
        import json
        from unittest.mock import patch
        from pipeline import step_final_validate
        from pipeline.content_quality import CHECKS,POLICY_VERSION,snapshot,source_evidence,confirmed_facts,requirements_for,ContentQualityError
        text='## まとめ\n完全無料で使えるサービスは存在しません。';outline='### H2：まとめ'
        source={'content_text':'[{"url":"https://example.com","status":"success","text":"女性は無料。男性は有料。"}]'}
        artifacts={'article':{'content_text':text},'outline':{'content_text':outline},'fact_sheet':{'content_text':FACTS},'content_contract':{'content_text':'{"required_sections":[]}'},'fresh_sources':source}
        report={'valid':True,'policy_version':POLICY_VERSION,'checks':[{'key':k,'reason':'確認','status':'pass'} for k in CHECKS],
                'snapshot':snapshot(text,confirmed_facts(FACTS),outline,{'required_sections':[]},requirements_for({},'比較'),source_evidence(source))}
        artifacts['content_audit']={'content_text':json.dumps(report)}
        with patch.object(step_final_validate,'get_artifact',side_effect=lambda _,s:artifacts[s]),patch.object(step_final_validate,'get_job',return_value={}),patch.object(step_final_validate,'upsert_artifact',side_effect=lambda **kw:kw) as save:
            with self.assertRaises(ContentQualityError):step_final_validate.run('j','比較')
        saved=json.loads(save.call_args.kwargs['content_text'])
        self.assertIn('missing_audience_condition',[i['key'] for i in saved['violations']])

    def test_outline_instruction_is_not_allowed_to_remove_audience(self):
        text='### H2：無料の正体\n- セクション内容：完全無料で出会い切れるアプリは基本的に存在せず、無料は確認までと説明する。'
        self.assertEqual(len(scope_issues(text,FACTS)),1)

    def test_free_feature_limit_also_requires_audience(self):
        self.assertEqual(len(scope_issues('無料でできるのは、登録からいいねを送るところまでです。',FACTS)),1)
        self.assertEqual(scope_issues('男性が無料でできるのは、登録からいいねを送るところまでです。',FACTS),[])
        self.assertEqual(scope_issues('「無料でできるのは検索までですか」と思う方もいます。',FACTS),[])

    def test_free_member_limit_requires_audience(self):
        self.assertEqual(len(scope_issues('無料会員でできるのは「登録・検索」までです。',FACTS)),1)
