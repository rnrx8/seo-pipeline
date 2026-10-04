import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.article_quality import parse_length_budget, validate_delivery
from pipeline.content_contract import build_content_contract
from pipeline.step_structure_guard import validate_structure
from pipeline.step_review import _word_count_action
from pipeline import research_requirements as req
from test_evidence_policy import fixture
from test_research_resume import message


class ProportionateGateTests(unittest.TestCase):
    def test_upper_only_has_no_invented_minimum_or_extra_margin(self):
        for setting in ('上限5,000字','5000字以内','5000字以下'):
            with self.subTest(setting=setting):
                budget=parse_length_budget(setting)
                self.assertEqual((budget.minimum,budget.maximum),(0,5000))
                self.assertIsNone(_word_count_action(1500,setting))
                self.assertIsNotNone(_word_count_action(5100,setting))
        self.assertGreater(parse_length_budget('5000〜7000字').minimum,0)
        self.assertEqual(parse_length_budget('5000字（上限6000字）').target,5000)

    def test_short_faq_passes_but_empty_faq_does_not(self):
        outline='### H2：よくある質問\n#### H3：女性は無料ですか\n'
        article='## よくある質問\n\n利用条件への回答です。\n\n### 女性は無料ですか\n\nはい。基本機能は無料です。'
        self.assertEqual(validate_delivery(article,outline),[])
        self.assertIn('empty_section',{i['key'] for i in validate_delivery(article.replace('はい。基本機能は無料です。',''),outline)})

    def test_state_word_in_table_is_not_a_placeholder(self):
        outline='### H2：本人確認\n#### H3：送信条件\n'
        article='## 本人確認\n\n利用条件です。\n\n### 送信条件\n\n| 状態 | 機能 |\n| --- | --- |\n| 本人確認が未確認の会員 | メッセージ送信不可 |'
        self.assertEqual(validate_delivery(article,outline),[])
        for placeholder in ('未確認','**要確認**','TBD','要確認（料金）'):
            self.assertIn('unfinished_table',{i['key'] for i in validate_delivery(article.replace('メッセージ送信不可',placeholder),outline)})

    def test_comparison_does_not_require_heading_magic_words(self):
        contract={'required_sections':[{'key':'named_service_comparison','candidate_services':['Aサービス','Bサービス'],'minimum_named_items':2}]}
        self.assertEqual(validate_structure('## 用途に合う候補を選ぶ\n\nAサービスとBサービスを比べます。',contract,outline=False),[])
        self.assertTrue(validate_structure('## 比較\n\nAサービスのみ',contract,outline=False))

    def test_single_service_plans_do_not_require_unrequested_competitors(self):
        contract=build_content_contract(keyword='Aサービス プラン比較',intent_text='Primary: プランを比較したい',
            query_attrs_text=None,fact_text='### Aサービス\n### Bサービス',job={},service={'name':'Aサービス'})
        self.assertNotIn('named_service_comparison',{s['key'] for s in contract['required_sections']})
        self.assertEqual(contract['required_sections'][0]['comparison_scope'],'plans')
        self.assertIn('comparison_coverage',{s['key'] for s in contract['required_sections']})

    def test_non_service_comparison_is_not_forced_into_three_services(self):
        contract=build_content_contract(keyword='ガラスと樹脂 比較',intent_text='Primary: 素材の違いを比較したい',
            query_attrs_text=None,fact_text='',job={})
        self.assertEqual(contract['required_sections'][0]['key'],'comparison_coverage')
        self.assertNotIn('minimum_named_items',contract['required_sections'][0])

    def test_explicit_count_promise_still_fails_when_items_are_missing(self):
        article='# おすすめ3選\n\n## 候補\n\n| サービス名 | 特徴 |\n| --- | --- |\n| A | a |\n| B | b |'
        self.assertIn('comparison_count_mismatch',{i['key'] for i in validate_delivery(article,'### H2：候補\n')})

    def test_priority_repair_is_only_a_candidate_until_independent_review_passes(self):
        for accepted in (True,False):
            with self.subTest(accepted=accepted):
                plan,_,_=fixture();plan['policy_sha256']='old-policy'
                plan['items'].append({**plan['items'][0],'id':'q2','question':'補助的な通知設定'})
                changes={'updates':[{**plan['items'][1],'priority':'supporting','required':False,
                                    'priority_reason':'ユーザーは料金比較を指定しており通知設定は補助'}],
                         'additions':[],'removals':[],'candidate_services':['A'],'scope_reason':'Aの比較'}
                records={'research_plan':{'content_text':json.dumps(plan),'meta':{}}}
                original=copy.deepcopy(records['research_plan'])
                usage=SimpleNamespace(input_tokens=0,output_tokens=0)
                verdicts=[({'valid':False,'issues':[{'id':'q2','reason':'AIによる過剰な必須分類'}]},usage),
                          ({'valid':accepted,'issues':[] if accepted else [{'id':'q2','reason':'ユーザー必須要件を失う'}]},usage)]
                with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}), \
                     patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(records[s])), \
                     patch.object(req,'get_optional_artifact',return_value=None), \
                     patch.object(req,'_plan_context',return_value={'requirements':{'custom_prompt':'必要な指定を維持'}}), \
                     patch.object(req,'upsert_artifact',side_effect=lambda **kw:records.update({kw['step']:copy.deepcopy(kw)}) or kw), \
                     patch.object(req,'create_with_retry',return_value=message(changes)), \
                     patch('pipeline.research_plan_review.review',side_effect=verdicts) as review:
                    if accepted:req.revalidate_plan('j','keyword')
                    else:
                        with self.assertRaises(req.ContentQualityError):req.revalidate_plan('j','keyword')
                self.assertEqual(review.call_count,2)
                self.assertFalse(json.loads(records['research_matrix']['content_text'])['valid'])
                if accepted:
                    saved=json.loads(records['research_plan']['content_text'])
                    self.assertTrue(saved['items'][0]['required'])
                    self.assertFalse(saved['items'][1]['required'])
                else:self.assertEqual(records['research_plan'],original)

    def test_unflagged_required_question_cannot_be_demoted(self):
        plan,_,_=fixture()
        change={'updates':[{**plan['items'][0],'required':False,'priority':'supporting'}],
                'additions':[],'candidate_services':['A'],'scope_reason':'A'}
        with self.assertRaises(req.ContentQualityError):req.apply_plan_repair(plan,change,reviewed_priority_ids={'other-id'})
