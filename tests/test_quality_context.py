import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pipeline.quality_context import review_requirements
from pipeline import content_quality as quality
from pipeline import focused_quality as focused


class QualityContextTests(unittest.TestCase):
    def test_shared_paragraph_keeps_language_and_organization_findings(self):
        checks=[{'affected_blocks':[{'id':'block-1','reason':'動詞の対応が不自然'},
                                    {'id':'block-2','reason':'説明が重複'}]},
                {'affected_blocks':[{'id':'block-1','reason':'結論が後置されている'},
                                    {'id':'block-1','reason':'動詞の対応が不自然'}]}]
        before=copy.deepcopy(checks)
        self.assertEqual(focused.merge_block_findings(checks),[
            {'id':'block-1','reason':'動詞の対応が不自然\n結論が後置されている'},
            {'id':'block-2','reason':'説明が重複'}])
        self.assertEqual(checks,before)

    def test_canonical_conditions_are_sent_once_and_noncanonical_facts_survive(self):
        from pipeline.quality_context import review_conditions
        from pipeline.claim_scope import conditional_facts
        facts = '男性は初回メッセージ無料。継続は月額100円。'
        requirements = self.requirements()
        requirements['research_decisions']['facts_sha256'] = quality.digest(facts)
        with patch('pipeline.quality_context.compact_enabled', return_value=True):
            self.assertIsInstance(review_conditions(facts, requirements), str)
            changed = facts + '追加プランは200円。'
            self.assertEqual(review_conditions(changed, requirements), conditional_facts(changed))
            requirements['research_decisions']['valid'] = False
            self.assertEqual(review_conditions(facts, requirements), conditional_facts(facts))

    def test_research_request_does_not_repeat_canonical_condition_statements(self):
        class Captured(Exception): pass
        facts = '男性は初回無料、以降100円。'
        requirements = self.requirements()
        requirements['research_decisions'].update(facts_sha256=quality.digest(facts))
        requirements['research_decisions']['items'][0]['answer'] = facts
        def capture(job, step, request):
            payload = json.loads(request['messages'][0]['content'])
            self.assertEqual(payload['requirements']['research_decisions']['items'][0]['answer'], facts)
            self.assertIsInstance(payload['conditional_facts'], str)
            self.assertNotIn(facts, payload['conditional_facts'])
            raise Captured()
        with patch('pipeline.quality_context.compact_enabled', return_value=True), \
             patch('pipeline.ai.tiered_review_enabled', return_value=True), \
             patch('pipeline.tiered_research.checked_request', side_effect=capture):
            with self.assertRaises(Captured):
                quality.audit(None, stage='research', text='# 構成', facts=facts, outline='# 構成',
                              contract={}, requirements=requirements, sources='資料')

    def requirements(self):
        return {'custom_prompt':'自社を優先。ただし料金条件を明記','learned_style_rules':['自然な日本語'],
                'research_plan':{'items':[{'id':'q1','question':'料金条件','required':True}],
                                 'candidate_services':['A'],'scope_reason':'対象','reader_need':'条件を知りたい','policy_sha256':'runtime'},
                'research_decisions':{'items':[{'id':'q1','answer':'1年契約','evidence':[{'quote':'1年契約'}]}],
                                      'coverage_sufficient':True,'coverage_issues':[], 'valid':True,
                                      'policy_sha256':'runtime'}}

    def test_role_projection_preserves_user_settings_and_all_evidence(self):
        original=self.requirements();before=copy.deepcopy(original)
        for role in focused.ROLES:
            projected=review_requirements(original,role)
            self.assertEqual(projected['custom_prompt'],original['custom_prompt'])
            self.assertEqual(projected['learned_style_rules'],original['learned_style_rules'])
            if role in ('evidence','coverage'):
                for key in ('research_plan','research_decisions'):
                    self.assertEqual(projected[key]['items'],original[key]['items'])
                    self.assertNotIn('policy_sha256',projected[key])
                self.assertEqual(projected['research_plan']['reader_need'],'条件を知りたい')
            else:
                self.assertNotIn('research_plan',projected)
                self.assertNotIn('research_decisions',projected)
        self.assertEqual(original,before)

    def test_every_final_role_still_reads_all_article_blocks(self):
        requests=[]
        def send(job,step,request):
            requests.append((step,json.loads(request['messages'][0]['content'])))
            keys=focused.ROLES[step.removeprefix('tiered_article_')]
            return {'checks':[{'key':k,'status':'pass','reason':'fixture','affected_blocks':[]} for k in keys]},NS(input_tokens=0,output_tokens=0)
        text='# 見出し\n\n文章の冒頭です。\n\n## 比較\n\n文章の末尾です。'
        with patch('pipeline.ai.tiered_review_enabled',return_value=True),patch('pipeline.tiered_research.checked_request',side_effect=send):
            focused.audit_article(None,text=text,facts='料金の根拠',outline='構成',contract={},requirements=self.requirements(),sources='原資料')
        self.assertEqual(len(requests),5)
        blocks=focused.content_blocks(text)
        for role,payload in requests:
            self.assertEqual(payload['article_blocks'],blocks)
            self.assertEqual(payload['requirements']['custom_prompt'],self.requirements()['custom_prompt'])
            if role=='tiered_article_evidence':self.assertEqual(payload['source_documents'],'原資料')

    def test_identical_outline_is_sent_once_distinct_outline_preserved(self):
        class Captured(Exception):pass
        for outline in ('構成本文','別の構成'):
            def capture(*args,**kw):
                payload=json.loads(kw['messages'][0]['content'])
                self.assertEqual(payload['document'],'構成本文')
                if outline=='構成本文':
                    self.assertNotIn('outline',payload);self.assertTrue(payload['outline_is_document'])
                else:self.assertEqual(payload['outline'],outline)
                raise Captured()
            with patch.object(quality,'create_with_retry',side_effect=capture):
                with self.assertRaises(Captured):
                    quality.audit(None,stage='research',text='構成本文',facts='',outline=outline,contract={},requirements=self.requirements(),sources='原資料')
