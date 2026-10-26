import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pipeline import research_requirements as req, research_answer_repair as repair, tiered_research as tr
from test_claim_evidence import fixture


class VerifiedAnswerRepairTests(unittest.TestCase):
    def fixture(self):
        plan,pages,value=fixture()
        req.validate_matrix(value,plan,pages)
        value.update(coverage_sufficient=False,coverage_reason='説明不足',
                     coverage_issues=[{'id':'q1','repair_kind':'editorial','reason':'確認済み条件から選び方を示す'}])
        return plan,pages,value

    def test_published_supporting_claim_keeps_source_in_full_coverage(self):
        plan,pages,value=fixture()
        value['items'][0]['claims'][0]['required_for_answer']=False
        req.validate_matrix(value,plan,pages)
        row=tr.coverage_decision(value['items'][0])
        self.assertEqual(row['claims'][0]['evidence'][0]['quote'],pages[0]['text'])
        self.assertFalse(row['claims'][0]['required_for_answer'])
        value['items'][0]['claim_states'][0].update(publishable=False)
        self.assertEqual(tr.coverage_decision(value['items'][0])['claims'],[])

    def test_projection_recheck_can_pass_without_paying_for_answer_repair(self):
        plan,pages,value=self.fixture()
        with patch.object(tr,'review_coverage',return_value=({'coverage_sufficient':True,'coverage_reason':'全根拠あり','coverage_issues':[]},NS(input_tokens=1,output_tokens=2))) as coverage,patch.object(tr,'checked_request') as send:
            result,usage=repair.run('j',value,plan,pages,{})
        coverage.assert_called_once();send.assert_not_called()
        self.assertFalse(req.validate_matrix(result,plan,pages))
        self.assertTrue(result['coverage_sufficient'])
        self.assertFalse(repair.eligible(result,plan,pages))
        self.assertEqual(usage.input_tokens,1)
        self.assertFalse(value['coverage_sufficient'])

    def test_repairs_only_named_answers_then_requires_independent_coverage(self):
        plan,pages,value=self.fixture()
        plan['items'].append({**plan['items'][0],'id':'q2'})
        other=copy.deepcopy(value['items'][0]);other['id']='q2';value['items'].append(other)
        req.validate_matrix(value,plan,pages)
        original_other=copy.deepcopy(value['items'][1])
        candidate={'items':[copy.deepcopy(value['items'][0])]}
        # A proposal may be added without pretending it is a quoted fact.
        proposal=copy.deepcopy(candidate['items'][0]['claims'][0])
        proposal.update(text='利用目的に合う条件を確認してから選ぶ。',answer_type='proposal',basis='guidance',evidence_indices=[],conditions=[])
        candidate['items'][0]['claims'].append(proposal)
        outcomes=[({'coverage_sufficient':False,'coverage_reason':'選択案が不足','coverage_issues':value['coverage_issues']},NS(input_tokens=1,output_tokens=1)),
                  ({'coverage_sufficient':True,'coverage_reason':'選択可能','coverage_issues':[]},NS(input_tokens=2,output_tokens=2))]
        with patch.object(tr,'review_coverage',side_effect=outcomes) as coverage,patch.object(tr,'checked_request',return_value=(candidate,NS(input_tokens=3,output_tokens=3))) as send,patch('pipeline.source_spans.expand_references',side_effect=lambda v,i:v):
            result,usage=repair.run('j',value,plan,pages,{})
        self.assertEqual(coverage.call_count,2)
        payload=json.loads(send.call_args.args[2]['messages'][0]['content'])
        self.assertEqual([q['id'] for q in payload['plan']['items']],['q1'])
        self.assertEqual(result['items'][1],original_other)
        self.assertFalse(req.validate_matrix(result,plan,pages))
        self.assertIn('確認してから',result['items'][0]['answer'])
        self.assertEqual(usage.input_tokens,6)

    def test_never_repairs_unknown_facts_or_source_gaps_or_previous_repair(self):
        for variant in ('source','unverified','already_repaired'):
            plan,pages,value=self.fixture()
            if variant=='source':value['coverage_issues'][0]['repair_kind']='source'
            elif variant=='unverified':value['items'][0]['claims'][0]['evidence_indices']=[]
            else:value.update(answer_repair_version=repair.VERSION,answer_repair_rounds=repair.MAX_ROUNDS)
            with self.subTest(variant=variant),patch.object(tr,'checked_request') as send:
                self.assertFalse(repair.eligible(value,plan,pages))
                with self.assertRaises(req.ContentQualityError):repair.run('j',value,plan,pages,{})
                send.assert_not_called()

    def test_failed_independent_check_is_not_promoted_or_repaired_again(self):
        plan,pages,value=self.fixture()
        # A newly identified source gap is not eligible for editorial repair.
        coverage={'coverage_sufficient':False,'coverage_reason':'主張の原典が必要','coverage_issues':[{'id':'q1','repair_kind':'source','reason':'原典不足'}]}
        with patch.object(tr,'review_coverage',return_value=(coverage,NS(input_tokens=1,output_tokens=1))),patch.object(tr,'checked_request') as send:
            result,_=repair.run('j',value,plan,pages,{})
        send.assert_not_called();self.assertTrue(req.validate_matrix(result,plan,pages))
        self.assertFalse(repair.eligible(result,plan,pages))

    def test_completed_factual_review_enters_answer_repair_at_any_attempt(self):
        for attempt in (1,2,3):
            with self.subTest(attempt=attempt):
                self._resume_verified_handoff(attempt)

    def _resume_verified_handoff(self, attempt):
        plan,pages,value=self.fixture()
        facts='original facts'
        value.update(valid=False,attempt=attempt,policy_sha256='previous-policy',
                     plan_sha256=req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
                     material_revision=req.material_revision(pages,facts))
        store={'research_matrix':{'content_text':json.dumps(value)},
               'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':facts},
               'serp':{'content_text':'serp'},'search_intent':{'content_text':'intent'}}
        def save(**kw):store[kw['step']]=copy.deepcopy(kw);return kw
        with patch.object(req,'load_plan',return_value=plan),patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(store[s])),patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch.object(req,'upsert_artifact',side_effect=save),patch('pipeline.ai.tiered_review_enabled',return_value=True),patch('pipeline.research_collection.recover_empty_collections',return_value=False),patch('pipeline.research_verification.audit_matrix',side_effect=AssertionError('must not replay all subjects')),patch.object(req,'supplement_once',side_effect=AssertionError('must not recollect sources')),patch.object(tr,'review_coverage',return_value=({'coverage_sufficient':True,'coverage_reason':'evidence retained','coverage_issues':[]},NS(input_tokens=1,output_tokens=1))):
            saved=req.verify('j','kw')
            self.assertTrue(saved['meta']['valid'])
            self.assertEqual(json.loads(saved['content_text'])['attempt'],attempt)
            self.assertEqual(store['research_matrix_before_answer_repair']['content_text'],json.dumps(value))
            self.assertTrue(req._require_matrix_strict('j')['valid'])

    def test_new_factual_review_repairs_handoff_before_any_supplement_or_rescreen(self):
        plan,pages,value=self.fixture()
        store={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'},
               'serp':{'content_text':'serp'},'search_intent':{'content_text':'intent'}}
        def save(**kw):store[kw['step']]=copy.deepcopy(kw);return kw
        with patch.object(req,'load_plan',return_value=plan),patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(store[s])),patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch.object(req,'upsert_artifact',side_effect=save),patch('pipeline.ai.tiered_review_enabled',return_value=True),patch('pipeline.research_collection.recover_empty_collections',return_value=False),patch.object(req,'refresh_dynamic_sources'),patch('pipeline.research_verification.audit_matrix',return_value=(value,NS(input_tokens=2,output_tokens=3))) as audit,patch.object(req,'supplement_once',side_effect=AssertionError('no missing source')),patch.object(req,'resolve_current_findings',side_effect=AssertionError('no rescreen')),patch.object(tr,'review_coverage',return_value=({'coverage_sufficient':True,'coverage_reason':'evidence retained','coverage_issues':[]},NS(input_tokens=1,output_tokens=1))):
            saved=req.verify('j','kw')
        audit.assert_called_once()
        self.assertTrue(saved['meta']['valid'])
        self.assertEqual(json.loads(saved['content_text'])['attempt'],1)
        self.assertEqual(saved['meta']['input_tokens'],3)

    def test_evolving_findings_repair_only_remaining_rounds_and_stop_on_pass(self):
        plan,pages,value=self.fixture()
        value.update(answer_repair_version='verified-answer-repair-v1')
        calls=[]
        def once(job,current,*args):
            calls.append(copy.deepcopy(current))
            result=copy.deepcopy(current)
            result['answer_repair_rounds']=len(calls)+1
            result['items'][0]['answer']='changed answer '+str(len(calls))
            if len(calls)==1:
                result['coverage_issues']=[{'id':'q1','repair_kind':'editorial','reason':'a newly found inconsistent row'}]
            else:result.update(coverage_sufficient=True,coverage_issues=[])
            return result,NS(input_tokens=1,output_tokens=1)
        with patch.object(repair,'_run_once',side_effect=once):result,usage=repair.run('j',value,plan,pages,{})
        self.assertEqual(len(calls),2)
        self.assertTrue(result['coverage_sufficient'])
        self.assertEqual(result['answer_repair_rounds'],3)
        self.assertEqual(usage.input_tokens,2)
        self.assertFalse(repair.eligible(result,plan,pages))

    def test_repair_interruption_keeps_completed_factual_review_for_resume(self):
        plan,pages,value=self.fixture()
        store={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'},
               'serp':{'content_text':'serp'},'search_intent':{'content_text':'intent'}}
        def save(**kw):store[kw['step']]=copy.deepcopy(kw);return kw
        with patch.object(req,'load_plan',return_value=plan),patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(store[s])),patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch.object(req,'upsert_artifact',side_effect=save),patch('pipeline.ai.tiered_review_enabled',return_value=True),patch('pipeline.research_collection.recover_empty_collections',return_value=False),patch.object(req,'refresh_dynamic_sources'),patch('pipeline.research_verification.audit_matrix',return_value=(value,NS(input_tokens=2,output_tokens=3))),patch.object(repair,'run',side_effect=RuntimeError('provider unavailable')):
            with self.assertRaisesRegex(RuntimeError,'provider unavailable'):req.verify('j','kw')
        failed=json.loads(store['research_matrix']['content_text'])
        self.assertEqual(failed['attempt'],1)
        self.assertFalse(failed['valid'])
        self.assertTrue(all(i['verified'] for i in failed['items']))
        self.assertEqual(store['research_matrix']['content_text'],store['research_matrix_before_answer_repair']['content_text'])
        self.assertEqual(store['fact_sheet']['content_text'],'facts')

    def test_unchanged_answer_and_repeated_findings_cannot_authorize_replay(self):
        plan,pages,value=self.fixture()
        unchanged=copy.deepcopy(value);unchanged['answer_repair_rounds']=1
        with patch.object(repair,'_run_once',return_value=(unchanged,NS(input_tokens=1,output_tokens=1))) as ask:
            result,_=repair.run('j',value,plan,pages,{})
        ask.assert_called_once()
        self.assertIn('unchanged',result['answer_repair_stopped_reason'])
        self.assertFalse(repair.eligible(result,plan,pages))

    def test_exhausted_early_answer_repair_cannot_fall_back_to_full_rescreen(self):
        plan,pages,value=self.fixture()
        value.update(valid=False,attempt=1,answer_repair_stopped_reason='unchanged_findings_or_answers',
            plan_sha256=req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
            material_revision=req.material_revision(pages,'facts'))
        store={'research_matrix':{'content_text':json.dumps(value)},
               'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        with patch.object(req,'load_plan',return_value=plan),patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(store[s])),patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(store.get(s))),patch('pipeline.ai.tiered_review_enabled',return_value=True),patch('pipeline.research_collection.recover_empty_collections',return_value=False),patch('pipeline.research_verification.audit_matrix',side_effect=AssertionError('no paid rescreen')),patch.object(req,'supplement_once',side_effect=AssertionError('no repeated retrieval')):
            with self.assertRaises(req.ContentQualityError):req.verify('j','kw')
