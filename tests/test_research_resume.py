import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_evidence_policy import fixture
from pipeline import research_verification as r


def message(value):
    return SimpleNamespace(stop_reason="end_turn",content=[SimpleNamespace(text=json.dumps(value))],
                           usage=SimpleNamespace(input_tokens=3,output_tokens=4))


class ResearchResumeTests(unittest.TestCase):
    def run_case(self, change=None, unresolved=False):
        plan,pages,result=fixture()
        if unresolved:result['items'][0].update(status='unresearched',basis='unresolved')
        saved={};calls=[]
        def create(*args,**kw):
            calls.append(kw)
            return message(result if '今回は対象別' in kw['system'] else {
                'coverage_sufficient':not unresolved,'coverage_reason':'test'})
        def save(**kw):saved[kw['step']]=copy.deepcopy(kw);return kw
        with patch.object(r,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))), \
             patch.object(r,'upsert_artifact',side_effect=save),patch.object(r,'create_with_retry',side_effect=create):
            r.audit_matrix('j',None,plan,pages,'facts','model',100)
            if change:change(plan,pages,saved)
            final,usage=r.audit_matrix('j',None,plan,pages,'facts','model',100)
        return calls,final,usage

    def test_same_request_reuses_subject_but_rechecks_overall(self):
        calls,_,usage=self.run_case()
        self.assertEqual(len(calls),3)
        self.assertEqual(usage.input_tokens,3)
        self.assertEqual(usage.output_tokens,4)

    def test_failed_check_is_preserved_not_promoted_to_pass(self):
        calls,value,_=self.run_case(unresolved=True)
        self.assertEqual(len(calls),3)
        self.assertFalse(value['items'][0]['verified'])
        self.assertFalse(value['coverage_sufficient'])

    def test_changed_source_or_plan_requires_fresh_review(self):
        for change in (lambda p,s,c:s[0].update(text='new source'),
                       lambda p,s,c:p['items'][0].update(question='別の条件')):
            with self.subTest(change=change):
                calls,_,_=self.run_case(change)
                self.assertEqual(len(calls),4)

    def test_tampered_or_legacy_result_is_not_reused(self):
        for change in (lambda p,s,c:c['research_check_1'].update(content_text='{}'),
                       lambda p,s,c:c['research_check_1'].update(meta={})):
            with self.subTest(change=change):
                calls,_,_=self.run_case(change)
                self.assertEqual(len(calls),4)

class PlanPolicyResumeTests(unittest.TestCase):
    def run_revalidation(self, valid):
        from pipeline import research_requirements as req
        plan,_,_=fixture();plan['policy_sha256']='old-policy'
        original=json.dumps(plan)
        old_hash=req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True))
        records={'research_plan':{'content_text':original,'meta':{}},
                 'research_collection_1':{'step':'research_collection_1','content_type':'text/markdown','content_text':'Raw research stays unchanged',
                    'meta':{'plan_sha256':old_hash}}}
        def save(**kw):records[kw['step']]=kw;return kw
        usage=SimpleNamespace(input_tokens=1,output_tokens=2)
        with patch.object(req,'get_artifact',side_effect=lambda j,s:copy.deepcopy(records[s])), \
             patch.object(req,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(records.get(s))), \
             patch.object(req,'upsert_artifact',side_effect=save),patch.object(req,'_plan_context',return_value={}), \
             patch.object(req.anthropic,'Anthropic'),patch.object(req,'create_with_retry',return_value=message({'updates':[],'additions':[]})), \
             patch('pipeline.research_plan_review.review',return_value=({'valid':valid,'issues':[]},usage)):
            if valid:req.revalidate_plan('j','keyword')
            else:
                with self.assertRaises(ValueError):req.revalidate_plan('j','keyword')
        return plan,records

    def test_review_pass_preserves_questions_and_notes_but_invalidates_matrix(self):
        from pipeline import research_requirements as req
        plan,records=self.run_revalidation(True)
        updated=json.loads(records['research_plan']['content_text'])
        self.assertEqual(plan['items'],updated['items'])
        self.assertEqual(updated['policy_sha256'],req.plan_policy())
        note=records['research_collection_1']
        self.assertEqual(note['content_text'],'Raw research stays unchanged')
        self.assertEqual(note['meta']['plan_sha256'],req.digest(json.dumps(updated,ensure_ascii=False,sort_keys=True)))
        self.assertFalse(json.loads(records['research_matrix']['content_text'])['valid'])

    def test_failed_review_cannot_refresh_old_plan_policy(self):
        _,records=self.run_revalidation(False)
        self.assertEqual(json.loads(records['research_plan']['content_text'])['policy_sha256'],'old-policy')
        self.assertNotIn('policy_revalidated',records['research_collection_1']['meta'])
        self.assertFalse(json.loads(records['research_matrix']['content_text'])['valid'])

    def test_targeted_plan_repair_preserves_ids_and_does_not_relabel_old_collection(self):
        from pipeline import research_requirements as req
        plan,_,_=fixture();plan['policy_sha256']='old'
        old_hash=req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True))
        note={'step':'research_collection_1','content_type':'text/markdown','content_text':'original', 'meta':{'plan_sha256':old_hash}}
        saved={};update={**plan['items'][0],'question':'通常料金'}
        addition={**update,'id':'q2','question':'期間限定トライアル','priority':'important','required':False}
        verdicts=[({'valid':False,'issues':[{'id':'q1','reason':'補助条件を分離'}]},SimpleNamespace(input_tokens=1,output_tokens=1)),
                  ({'valid':True,'issues':[]},SimpleNamespace(input_tokens=1,output_tokens=1))]
        with patch.object(req,'get_artifact',return_value={'content_text':json.dumps(plan),'meta':{}}), \
             patch.object(req,'get_optional_artifact',return_value=note),patch.object(req,'_plan_context',return_value={}), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:kw}) or kw), \
             patch.object(req.anthropic,'Anthropic'),patch.object(req,'create_with_retry',return_value=message({'updates':[update],'additions':[addition]})) as generate, \
             patch('pipeline.research_plan_review.review',side_effect=verdicts) as review:
            req.revalidate_plan('j','keyword')
        value=json.loads(saved['research_plan']['content_text'])
        self.assertEqual([i['id'] for i in value['items']],['q1','q2'])
        self.assertTrue(value['items'][0]['required']);self.assertFalse(value['items'][1]['required'])
        self.assertNotIn('research_collection_1',saved)
        self.assertEqual(generate.call_count,1);self.assertEqual(review.call_count,2)
