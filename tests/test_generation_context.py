import copy
import json
import os
import unittest
from unittest.mock import patch
from test_evidence_policy import fixture
from pipeline.generation_context import decision_bundle,generation_evidence
from pipeline.quality_context import review_facts,decision_brief
from pipeline.content_quality import ContentQualityError,digest

class GenerationContextTests(unittest.TestCase):
    def data(self):
        plan,pages,matrix=fixture()
        matrix['valid']=True
        matrix['items'][0]['verified']=True
        matrix['items'][0]['future_condition']={'audience':'男性','term':'2025年のみ'}
        return plan,pages,matrix

    def test_deduplicates_references_without_losing_answers_dates_or_new_conditions(self):
        plan,pages,matrix=self.data()
        plan['items'].append({**plan['items'][0],'id':'q2'})
        matrix['items'].append({**copy.deepcopy(matrix['items'][0]),'id':'q2'})
        original=copy.deepcopy(matrix)
        body=decision_bundle(plan,matrix,quotes=True)
        data=json.loads(body[body.index('{'):])
        self.assertEqual(len(data['sources']),2)
        self.assertEqual(data['decisions'][0]['source_ids'],data['decisions'][1]['source_ids'])
        self.assertEqual(data['decisions'][0]['answer'],matrix['items'][0]['answer'])
        self.assertEqual(data['decisions'][0]['future_condition'],matrix['items'][0]['future_condition'])
        self.assertFalse(data['decisions'][0]['supports_current_conclusion'])
        self.assertEqual(data['sources']['ref1']['quote'],pages[1]['text'])
        self.assertEqual(matrix,original)

    def test_outline_keeps_source_urls_but_no_quote_bodies(self):
        plan,pages,matrix=self.data()
        data=decision_bundle(plan,matrix,quotes=False)
        self.assertIn(pages[1]['url'],data)
        self.assertNotIn('"quote"',data)
        self.assertIn('2025年1月',data)

    def test_invalid_or_missing_question_cannot_become_writing_context(self):
        plan,pages,matrix=self.data()
        for change in ({'valid':False},{'items':[]}):
            with self.assertRaises(ContentQualityError):decision_bundle(plan,{**matrix,**change},quotes=True)
        matrix['items'][0]['verified']=False
        with self.assertRaises(ContentQualityError):decision_bundle(plan,matrix,quotes=True)

    def test_production_boundary_requires_revision_check_and_legacy_stays_unchanged(self):
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}),patch('pipeline.research_requirements.require_matrix',side_effect=ContentQualityError('stale')) as check:
            with self.assertRaises(ContentQualityError):generation_evidence('job','facts','sources')
            check.assert_called_once_with('job')
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'astra'}),patch('pipeline.content_quality.writing_evidence',return_value='legacy') as old:
            self.assertEqual(generation_evidence('job','facts','sources'),'legacy')
            old.assert_called_once_with('facts','sources')

    def test_canonical_facts_can_reference_decisions_but_extra_facts_are_retained(self):
        facts='男性のみ、2025年価格。\n出典：https://source.example｜確認箇所："原文"'
        requirements={'research_decisions':{'valid':True,'items':[{'answer':'男性のみ、2025年価格。'}],'facts_sha256':digest(facts)}}
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}):
            self.assertIn('requirements.research_decisions',review_facts(facts,requirements))
            different=review_facts(facts+'\n追加条件を保持',requirements)
            self.assertIn('男性のみ、2025年価格。',different)
            self.assertIn('追加条件を保持',different)
            self.assertIn('https://source.example',different)
            self.assertNotIn('"原文"',different)

    def test_unresolved_candidates_do_not_leak_quotes_back_to_reviewers(self):
        result=decision_brief({'research_decisions':{'items':[{'answer':'保持','unresolved_candidate':{'evidence':[{'quote':'huge'}]}}]}})
        self.assertEqual(result['research_decisions']['items'][0],{'answer':'保持'})

    def test_real_generation_entrypoints_do_not_reintroduce_full_sources_or_matrix(self):
        from contextlib import ExitStack
        from pipeline import step_outline,step_article,step_service_map
        from test_article_quality import OUTLINE,CONTRACT
        plan,pages,matrix=self.data()
        pages.append({'url':'https://unrelated.example','status':'success','text':'UNRELATED_BODY_SENTINEL'})
        matrix['runtime_debug_marker']='MATRIX_BOOKKEEPING_SENTINEL'
        outline,_=step_outline.ensure_complete_volume_design(OUTLINE,'5,000字')
        artifacts={'serp':{'content_text':'{}'},'search_intent':{'content_text':'読者の料金比較'},
                   'outline':{'content_text':outline},'fact_sheet':{'content_text':'男性のみ [confirmed]'},
                   'fresh_sources':{'content_text':json.dumps(pages)},'content_contract':{'content_text':json.dumps(CONTRACT)},
                   'research_validation':{'content_text':'{}'}}
        class Captured(Exception):pass
        for module in (step_outline,step_article,step_service_map):
            captured=[]
            def send(*args,**kwargs):
                messages=kwargs.get('messages',args[1] if len(args)>1 else [])
                captured.append(json.dumps(messages,ensure_ascii=False))
                raise Captured()
            with self.subTest(stage=module.__name__),ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}))
                stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
                stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
                stack.enter_context(patch.object(module,'get_artifact',side_effect=lambda j,k:artifacts[k]))
                stack.enter_context(patch.object(module,'get_job',return_value={'word_count_setting':'5,000字'}))
                stack.enter_context(patch.object(module.anthropic,'Anthropic'))
                if module==step_article:
                    stack.enter_context(patch.object(module,'require_audit'))
                    stack.enter_context(patch.object(module,'requirements_for',return_value={}))
                    stack.enter_context(patch.object(module,'_call',side_effect=send))
                else:stack.enter_context(patch.object(module,'create_with_retry',side_effect=send))
                with self.assertRaises(Captured):module.run('job','比較')
            self.assertEqual(len(captured),1)
            self.assertNotIn('UNRELATED_BODY_SENTINEL',captured[0])
            self.assertNotIn('MATRIX_BOOKKEEPING_SENTINEL',captured[0])
            self.assertIn('2025年1月',captured[0])
            self.assertIn('https://media-one.example/a',captured[0])
