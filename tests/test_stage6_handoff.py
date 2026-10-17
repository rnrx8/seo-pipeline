"""Regression tests for actual live-run handoff failures, with no model/network IO."""
import copy
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import research_requirements as req, editorial_blueprint as bp, step_outline
from test_editorial_blueprint import fixture
from test_evidence_policy import fixture as research_fixture
import test_focused_research as focused_tests


class ResearchFindingResolutionTests(unittest.TestCase):
    def setUp(self):
        self.plan,self.pages,self.result=research_fixture()
        self.plan['items'].append({**self.plan['items'][0],'id':'q2'})
        self.records={};self.saves=[]
        self.matrix={'valid':False,'attempt':2,'plan_sha256':req.digest(json.dumps(self.plan,ensure_ascii=False,sort_keys=True)),
                     'coverage_issues':[{'id':'q1','repair_kind':'editorial','reason':'compare existing facts'},
                                        {'id':'q2','repair_kind':'source','reason':'missing source'},
                                        {'id':'unknown','repair_kind':'source','reason':'out of scope'}]}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(self.records.get(s))))
        def save(**kw):
            self.saves.append(copy.deepcopy(kw));self.records[kw['step']]=copy.deepcopy(kw);return kw
        self.stack.enter_context(patch.object(req,'upsert_artifact',side_effect=save))
        self.fetch=self.stack.enter_context(patch('pipeline.step_fact_sheet.run'))

    def test_only_named_source_gaps_are_fetched_and_no_repeated_resolution(self):
        self.assertTrue(req.resolve_current_findings('j','kw',self.plan,self.matrix))
        self.assertEqual(self.fetch.call_args.kwargs['research_question_ids'],['q2'])
        passed=json.loads(self.fetch.call_args.kwargs['research_gaps'])
        self.assertEqual({i['id'] for i in passed},{'q1','q2'})
        self.assertFalse(req.resolve_current_findings('j','kw',self.plan,self.matrix))
        self.assertEqual(self.fetch.call_count,1)
        self.assertTrue(all(s['meta']['valid'] is False for s in self.saves))
        receipt=json.loads(self.saves[-1]['content_text'])
        self.assertEqual(receipt['acceptance'],'requires_fresh_full_verification')

    def test_new_source_question_can_be_fetched_without_repeating_previous_question(self):
        req.resolve_current_findings('j','kw',self.plan,self.matrix)
        self.matrix['coverage_issues']=[{'id':'q1','repair_kind':'source','reason':'new concrete source need'},
                                         {'id':'q2','repair_kind':'source','reason':'previous need'}]
        self.assertTrue(req.resolve_current_findings('j','kw',self.plan,self.matrix))
        self.assertEqual(self.fetch.call_args.kwargs['research_question_ids'],['q1'])
        state=json.loads(self.saves[-1]['content_text'])
        self.assertEqual(state['source_question_ids_attempted'],['q1','q2'])
        self.assertEqual(len(state['history']),1)
        self.assertFalse(req.resolve_current_findings('j','kw',self.plan,self.matrix))
        self.assertEqual(self.fetch.call_count,2)

    def test_editorial_feedback_does_not_trigger_source_collection(self):
        self.matrix['coverage_issues']=self.matrix['coverage_issues'][:1]
        self.assertTrue(req.resolve_current_findings('j','kw',self.plan,self.matrix))
        self.fetch.assert_not_called()

    def test_stale_plan_or_unfinished_resolution_is_not_retried(self):
        stale={**self.matrix,'plan_sha256':'old'}
        self.assertFalse(req.resolve_current_findings('j','kw',self.plan,stale))
        self.records['research_finding_resolution']={'content_text':json.dumps({'status':'running','plan_sha256':self.matrix['plan_sha256']})}
        with self.assertRaises(req.ContentQualityError):req.resolve_current_findings('j','kw',self.plan,self.matrix)
        self.records['research_finding_resolution']['content_text']=json.dumps({'status':'completed','plan_sha256':'old'})
        with self.assertRaises(req.ContentQualityError):req.resolve_current_findings('j','kw',self.plan,self.matrix)
        self.fetch.assert_not_called()

    def test_routed_collection_is_followed_by_fresh_full_audit(self):
        # Original one-question fixture keeps validation meaningful.
        plan,pages,value=research_fixture();failed={**self.matrix,'plan_sha256':req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
            'coverage_issues':[{'id':'q1','repair_kind':'source','reason':'needed evidence'}]}
        self.records['research_matrix']={'content_text':json.dumps(failed)}
        artifacts={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        events=[];self.fetch.side_effect=lambda *a,**kw:events.append('fetch')
        def audit(*a,**kw):events.append('full_audit');return value,SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch.object(req,'load_plan',return_value=plan),patch('pipeline.research_collection.recover_empty_collections',return_value=False), \
             patch.object(req,'refresh_dynamic_sources'),patch.object(req,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(req,'get_step_config',return_value=('gpt-6.1-sol',100)),patch('pipeline.research_verification.audit_matrix',side_effect=audit):
            saved=req.verify('j','kw')
        self.assertEqual(events,['fetch','full_audit']);self.assertTrue(saved['meta']['valid'])
        self.assertEqual(json.loads(saved['content_text'])['attempt'],3)

    def test_current_third_failure_stops_before_any_new_calls(self):
        self.records['research_matrix']={'content_text':json.dumps({**self.matrix,'attempt':3,'policy_sha256':req.matrix_policy()})}
        with patch.object(req,'load_plan',return_value=self.plan),patch('pipeline.research_collection.recover_empty_collections',return_value=False):
            with self.assertRaises(req.ContentQualityError):req.verify('j','kw')
        self.fetch.assert_not_called();self.assertFalse(self.saves)


class PlanProjectionRefreshTests(unittest.TestCase):
    def test_rebuilds_projection_before_independent_review_and_preserves_original(self):
        plan,_,_=research_fixture();original=json.dumps(plan);saved={};design={'answer_scope_policy':'current'}
        def save(**kw):saved[kw['step']]=kw;return kw
        def review(job,value,context,*args):
            self.assertEqual(value['reader_design'],design)
            return {'valid':True,'issues':[]},SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch.object(req,'get_artifact',return_value={'content_text':original,'meta':{}}), \
             patch.object(req,'get_optional_artifact',return_value=None),patch.object(req,'upsert_artifact',side_effect=save), \
             patch.object(req,'_plan_context',return_value={'reader_needs':{'selected':[]},'production_brief':{}}), \
             patch('pipeline.reader_needs.editorial_context',return_value=design),patch('pipeline.research_plan_review.review',side_effect=review) as check:
            req.revalidate_plan('j','kw')
        self.assertEqual(check.call_count,1)
        self.assertEqual(saved['research_plan_previous_policy']['content_text'],original)
        self.assertEqual(json.loads(saved['research_plan']['content_text'])['reader_design'],design)
        self.assertFalse(saved['research_matrix']['meta']['valid'])


class BlueprintProvenanceTests(unittest.TestCase):
    def test_accepted_claim_parent_is_derived_without_changing_raw_model(self):
        _,_,_,ctx,value=fixture();value['sections'][0]['research_ids']=[]
        original=copy.deepcopy(value)
        with self.assertRaises(req.ContentQualityError):bp.validate(value,ctx)
        normalized=bp.canonicalize_research_refs(value,ctx);bp.validate(normalized,ctx)
        self.assertEqual(normalized['sections'][0]['research_ids'],['q1'])
        self.assertEqual(value,original)

    def test_unknown_claim_or_unknown_declared_research_stays_invalid(self):
        _,_,_,ctx,value=fixture()
        value['sections'][0]['answer_elements'][0]['claim_ids']=['withheld.c01']
        with self.assertRaises(req.ContentQualityError):bp.canonicalize_research_refs(value,ctx)
        _,_,_,ctx,value=fixture();value['sections'][0]['research_ids']=['unknown']
        with self.assertRaises(req.ContentQualityError):bp.validate(bp.canonicalize_research_refs(value,ctx),ctx)

    def test_new_outline_stops_before_paid_generation_if_research_is_invalid(self):
        artifacts={'serp':{'content_text':'{}'},'search_intent':{'content_text':'intent','meta':{'reader_needs_version':'new'}}}
        with patch.object(step_outline,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch('pipeline.generation_context.authoring_intent',return_value='intent'), \
             patch.object(req,'require_matrix',return_value={}),patch.object(req,'_require_matrix_strict',side_effect=req.ContentQualityError('invalid')), \
             patch.object(step_outline,'create_with_retry') as send:
            with self.assertRaises(req.ContentQualityError):step_outline.run('j','kw')
        send.assert_not_called()


class FocusedFeedbackHandoffTests(focused_tests.FocusedResearchTests):
    # Reuse only the request-generating harness, not inherited tests.
    def test_verified_fact_with_coverage_feedback_still_reaches_independent_review(self):
        self.plan['reader_design']={'request':{'article_purpose':'inform comparison','target_audience':'readers comparing methods'}}
        self.pages[0]['text']='2025年1月の月額料金は2,000円です。'
        self.answer['items'][0].update(basis='primary',evidence=[{'url':self.pages[0]['url'],'quote':self.pages[0]['text'],'source_kind':'primary','independence_group':'official'}])
        issue={'id':self.plan['items'][0]['id'],'repair_kind':'editorial','reason':'answer the comparison question using confirmed features'}
        self.saved['research_matrix_2']={'content_text':json.dumps({'coverage_issues':[issue]})}
        def respond(request,payload):
            if 'decisions' in payload:return dict(coverage_sufficient=True,coverage_reason='fixture',coverage_issues=[])
            return {**copy.deepcopy(self.answer),'needs_more_sources':False}
        self.run_flow(respond)
        audits=[(r,p) for r,p in self.calls if 'candidate_answers' in p and r['model']=='gpt-6.1-sol']
        self.assertEqual(len(audits),1)
        self.assertEqual(audits[0][1]['previous_findings'],[issue])
        self.assertIn('items',audits[0][1]['article_scope'])
        self.assertIn('article_request',audits[0][1]['article_scope'])

    def test_focused_review_receives_relevant_previous_findings(self):
        issue={'id':self.plan['items'][0]['id'],'repair_kind':'editorial','reason':'use already verified comparison facts'}
        self.saved['research_matrix_2']={'content_text':json.dumps({'coverage_issues':[issue,{'id':'other','repair_kind':'source','reason':'other'}]})}
        def respond(request,payload):
            if 'candidate_answers' in payload and request['model']=='gpt-6.1-sol':
                visible={p['url'] for p in payload['sources']}
                if not {r['url'] for r in self.answer['items'][0]['evidence']}<=visible:
                    value=self.unknown();value['items'][0].update(additional_sources_needed=True)
                    return {**value,'needs_more_sources':True}
            return self.responder(request,payload)
        self.run_flow(respond)
        focused=[p for r,p in self.calls if 'candidate_answers' in p]
        self.assertTrue(focused)
        self.assertTrue(all(p['previous_findings']==[issue] for p in focused))

    def test_common_question_receives_verified_facts_without_naming_the_company(self):
        self.plan['items'].append({**self.plan['items'][0],'id':'q2','subject':'共通','question':'方法をどう比較するか'})
        def respond(request,payload):
            if 'decisions' in payload:return dict(coverage_sufficient=True,coverage_reason='fixture',coverage_issues=[])
            rows=[{**copy.deepcopy(self.answer['items'][0]),'id':q['id']} for q in payload['plan']['items']]
            if payload['plan']['items'][0]['subject']=='共通':
                related=payload['related_verified_answers'];self.assertEqual(related[0]['id'],self.plan['items'][0]['id'])
                self.assertEqual(related[0]['answer'],self.answer['items'][0]['answer'])
                self.assertNotIn('quote',related[0]['evidence'][0])
            return {'items':rows,'needs_more_sources':False}
        self.run_flow(respond)

# Avoid running the reused harness's inherited cases a second time.
for _name in tuple(vars(focused_tests.FocusedResearchTests)):
    if _name.startswith('test_'):setattr(FocusedFeedbackHandoffTests,_name,None)
