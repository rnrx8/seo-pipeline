from pipeline.outline_policy import current_policy
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
    def setUp(self):
        policy=patch("pipeline.research_requirements.matrix_policy",return_value="current-test-policy")
        policy.start();self.addCleanup(policy.stop)

    def data(self):
        plan,pages,matrix=fixture()
        matrix['valid']=True
        matrix['policy_sha256']='current-test-policy'
        matrix['items'][0]['verified']=True
        matrix['items'][0]['future_condition']={'audience':'男性','term':'2025年のみ'}
        matrix['items'][0]['reviewed_source_urls']=['https://review-bookkeeping.example']
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

    def test_acceptance_metadata_is_not_article_content(self):
        plan,pages,matrix=self.data()
        matrix['items'][0]['reason']='ACCEPTANCE_REASON_ONLY'
        plan['items'][0]['priority_reason']='RESEARCH_PRIORITY_ONLY'
        original=copy.deepcopy(matrix)
        body=decision_bundle(plan,matrix,quotes=True)
        data=json.loads(body[body.index('{'):])
        row=data['decisions'][0]
        for field in ('basis','reason','applicable_at','omission_reason','source_requirement','priority_reason'):
            self.assertNotIn(field,row)
        self.assertNotIn('ACCEPTANCE_REASON_ONLY',body)
        self.assertNotIn('RESEARCH_PRIORITY_ONLY',body)
        self.assertEqual(data['usage_constraints']['q1']['applicable_at'],matrix['items'][0]['applicable_at'])
        self.assertEqual(row['answer'],matrix['items'][0]['answer'])
        self.assertFalse(row['supports_current_conclusion'])
        self.assertTrue(row['publishable'])
        self.assertNotIn('source_kind',data['sources']['ref1'])
        self.assertEqual(matrix,original)

    def test_outline_keeps_source_urls_but_no_quote_bodies(self):
        plan,pages,matrix=self.data()
        data=decision_bundle(plan,matrix,quotes=False)
        self.assertIn(pages[1]['url'],data)
        self.assertNotIn('"quote"',data)
        self.assertNotIn('review-bookkeeping.example',data)
        self.assertIn('2025年1月',data)

    def test_invalid_or_missing_question_cannot_become_writing_context(self):
        plan,pages,matrix=self.data()
        for change in ({'valid':False},{'items':[]}):
            with self.assertRaises(ContentQualityError):decision_bundle(plan,{**matrix,**change},quotes=True)
        matrix['items'][0]['verified']=False
        with self.assertRaises(ContentQualityError):decision_bundle(plan,matrix,quotes=True)

    def test_all_providers_require_validated_projected_evidence(self):
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}),patch('pipeline.research_requirements.require_matrix',side_effect=ContentQualityError('stale')) as check:
            with self.assertRaises(ContentQualityError):generation_evidence('job','facts','sources')
            check.assert_called_once_with('job')
        plan,_,matrix=self.data()
        for provider in ('astra','sonnet','tiered'):
            with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':provider}),patch('pipeline.research_requirements.require_matrix',return_value=matrix),patch('pipeline.research_requirements.load_plan',return_value=plan):
                self.assertIn('generation-evidence-v3',generation_evidence('job','facts','sources'))

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
                   'outline':{'content_text':outline,'meta':{'editorial_policy':current_policy()}},'fact_sheet':{'content_text':'男性のみ [confirmed]'},
                   'fresh_sources':{'content_text':json.dumps(pages)},'content_contract':{'content_text':json.dumps(CONTRACT)},
                   'research_validation':{'content_text':'{}'}}
        class Captured(Exception):pass
        import itertools
        for module,provider in itertools.product((step_outline,step_article,step_service_map),("tiered","astra","sonnet")):
            captured=[]
            def send(*args,**kwargs):
                messages=kwargs.get('messages',args[1] if len(args)>1 else [])
                captured.append(json.dumps(messages,ensure_ascii=False))
                raise Captured()
            with self.subTest(stage=module.__name__,provider=provider),ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':provider,'QUALITY_RESEARCH_ROUTING':'focused'}))
                stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
                stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
                stack.enter_context(patch.object(module,'get_artifact',side_effect=lambda j,k:artifacts[k]))
                stack.enter_context(patch.object(module,'get_job',return_value={'word_count_setting':'5,000字', **({'tenant_id':'tenant'} if module==step_article else {})}))
                stack.enter_context(patch.object(module.anthropic,'Anthropic'))
                if module==step_article:
                    stack.enter_context(patch('pipeline.db.get_learned_style_rules',return_value=[{'rule_text':'ACCOUNT_STYLE_SENTINEL'}]))
                    from pipeline import content_quality as cq
                    requirements={'intent_value_context':{'reader_desire':'料金を比較して選びたい'}}
                    stack.enter_context(patch.object(module,'intent_review_requirements',return_value=requirements))
                    readiness={'valid':True,'stage':'research','policy_version':cq.POLICY_VERSION,
                        'checks':[{'key':key,'status':'pass','reason':'fixture'} for key in cq.CHECKS],
                        'snapshot':cq.snapshot(outline,cq.confirmed_facts(artifacts['fact_sheet']['content_text']),outline,
                            CONTRACT,requirements,cq.source_evidence(artifacts['fresh_sources']))}
                    artifacts['research_validation']={'content_text':json.dumps(readiness)}
                    stack.enter_context(patch.object(module,'_call',side_effect=send))
                else:stack.enter_context(patch.object(module,'create_with_retry',side_effect=send))
                with self.assertRaises(Captured):module.run('job','比較')
                if module==step_article:
                    requirements['intent_value_context']['reader_desire']='確認後に検索意図が変更された'
                    with self.assertRaises(ContentQualityError):module.run('job','比較')
            self.assertEqual(len(captured),1)
            self.assertNotIn('UNRELATED_BODY_SENTINEL',captured[0])
            self.assertNotIn('MATRIX_BOOKKEEPING_SENTINEL',captured[0])
            self.assertIn('2025年1月',captured[0])
            self.assertIn('https://media-one.example/a',captured[0])
            if module==step_article:
                self.assertIn('ACCOUNT_STYLE_SENTINEL',captured[0])

    def test_repair_projection_preserves_conditions_sources_and_audit_record(self):
        from pipeline.generation_context import repair_requirements
        plan,_,matrix=self.data()
        matrix['items'][0]['reason']='INTERNAL_ADOPTION_SENTINEL'
        requirements={'research_plan':plan,'research_decisions':matrix,'unrelated_requirement':'keep'}
        original=copy.deepcopy(requirements)
        projected=repair_requirements(requirements)
        row=projected['research_decisions']['items'][0]
        self.assertEqual(row['answer'],matrix['items'][0]['answer'])
        self.assertEqual(row['future_condition'],matrix['items'][0]['future_condition'])
        self.assertEqual(row['evidence'][0]['url'],matrix['items'][0]['evidence'][0]['url'])
        self.assertEqual(row['evidence'][0]['quote'],matrix['items'][0]['evidence'][0]['quote'])
        self.assertFalse(row['supports_current_conclusion'])
        self.assertNotIn('INTERNAL_ADOPTION_SENTINEL',json.dumps(projected))
        self.assertNotIn('basis',row)
        self.assertEqual(projected['usage_constraints']['q1']['applicable_at'],matrix['items'][0]['applicable_at'])
        self.assertEqual(projected,repair_requirements(projected))
        self.assertEqual(requirements,original)

    def test_direct_projection_rejects_stale_answers_even_when_marked_valid(self):
        plan,_,matrix=self.data()
        for policy in (None,'old-policy'):
            matrix['policy_sha256']=policy
            with self.assertRaises(ContentQualityError):
                decision_bundle(plan,matrix,quotes=False)
