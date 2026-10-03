import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import tiered_research as tiered
from pipeline import focused_research as focused
from pipeline import quality_budget as budget
from pipeline.content_quality import ContentQualityError
from pipeline.research_requirements import matrix_policy, validate_matrix
from test_evidence_policy import fixture


class FocusedResearchTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered', 'QUALITY_RESEARCH_ROUTING':'focused'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.plan,self.pages,self.answer=fixture()
        self.calls=[];self.saved={}

    def unknown(self):
        value=copy.deepcopy(self.answer)
        value['items'][0].update(status='unresearched', basis='unresolved', answer='', evidence=[], verified=False)
        return value

    def run_flow(self, responder):
        def generate(*args, **request):
            payload=json.loads(request['messages'][0]['content'])
            self.calls.append((request,payload))
            result=copy.deepcopy(responder(request,payload))
            for item in result.get('items',[]):
                for ref in item['evidence']:
                    page=next(p for p in payload['sources'] if p['url']==ref['url'])
                    ref['source_ref']=next(e['source_ref'] for e in page['excerpts'] if ref['quote'] in e['text'])
                    ref.pop('quote');ref.pop('url')
                    ref['expert_source_ref']='';ref.pop('expert_qualification_quote',None)
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(result))],usage=NS(input_tokens=10,output_tokens=3))
        with patch.object(tiered,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(self.saved.get(s))), \
             patch.object(tiered,'upsert_artifact',side_effect=lambda **kw:self.saved.update({kw['step']:copy.deepcopy(kw)})), \
             patch.object(tiered,'create_with_retry',side_effect=generate):
            return tiered.audit_matrix('same-job',self.plan,self.pages,'',{})

    def responder(self, request, payload):
        if 'decisions' in payload:
            return dict(coverage_sufficient=True, coverage_reason='fixture', coverage_issues=[])
        if 'candidate_answers' not in payload:
            return self.unknown()
        if request['model']=='gpt-6-luna':
            return {**self.unknown(),'needs_more_sources':False}
        return {**self.answer,'needs_more_sources':False}

    def test_required_unknown_is_reread_once_then_remains_blocking(self):
        result,_=self.run_flow(self.responder)
        self.assertEqual([r['model'] for r,p in self.calls],['gpt-6-luna','gpt-6-luna','gpt-6.1-sol'])
        self.assertFalse(result['items'][0]['verified'])
        self.assertTrue(validate_matrix(result,self.plan,self.pages))
        self.assertEqual(set(self.saved),{'tiered_screen_1','tiered_focused_1_reread','tiered_coverage'})
        self.assertEqual([p['url'] for p in self.calls[1][1]['sources']],[self.pages[0]['url']])

    def test_same_resume_reuses_results_and_changed_source_invalidates(self):
        result,_=self.run_flow(self.responder)
        again,usage=self.run_flow(self.responder)
        self.assertEqual(result,again)
        self.assertEqual(usage.input_tokens,0)
        self.assertEqual(len(self.calls),3)
        self.pages[2]['text']+=' 条件が変わりました。'
        self.run_flow(self.responder)
        self.assertEqual(len(self.calls),5)  # unchanged whole-coverage result reused
        self.assertNotEqual(self.calls[1][1]['source_revision'],self.calls[4][1]['source_revision'])

    def test_known_complex_evidence_is_all_retained_and_extra_sources_can_expand_once(self):
        self.pages.append({'url':'https://other.example/terms','status':'success','text':'別の条件'})
        audits=[]
        def respond(request,payload):
            if 'decisions' in payload:return self.responder(request,payload)
            if 'candidate_answers' not in payload:return self.answer
            audits.append(payload)
            return {**self.answer,'needs_more_sources':True}
        result,_=self.run_flow(respond)
        self.assertEqual(len(audits),2)
        self.assertEqual({p['url'] for p in audits[0]['sources']},{p['url'] for p in self.pages[:3]})
        self.assertEqual(len(audits[0]['source_catalog']),4)
        self.assertFalse(audits[0]['sources_complete'])
        self.assertTrue(audits[1]['sources_complete'])
        self.assertEqual(len(audits[1]['sources']),4)
        self.assertFalse(result['items'][0]['verified'])
        self.assertTrue(validate_matrix(result,self.plan,self.pages))

    def test_reread_recovers_saved_price_without_search_and_then_audits_if_needed(self):
        self.pages[0]['text']='1ヶ月（30日）9,980円。税込。'
        self.plan['items'][0]['source_requirement']='primary_only'
        good=copy.deepcopy(self.answer)
        good['items'][0].update(basis='primary', answer=self.pages[0]['text'],
            evidence=[{'url':self.pages[0]['url'],'quote':self.pages[0]['text'],'source_kind':'primary'}])
        def respond(request,payload):
            if 'decisions' in payload:return self.responder(request,payload)
            if 'candidate_answers' not in payload:return self.unknown()
            return {**good,'needs_more_sources':False}
        result,_=self.run_flow(respond)
        self.assertEqual([r['model'] for r,p in self.calls],['gpt-6-luna','gpt-6-luna','gpt-6.1-sol','gpt-6.1-sol'])
        self.assertTrue(result['items'][0]['verified'])
        self.assertFalse(validate_matrix(result,self.plan,self.pages))
        self.assertTrue(all('tools' not in r for r,p in self.calls))

    def test_eligible_optional_omission_still_needs_overall_coverage(self):
        self.plan,self.pages,self.answer=fixture('supporting')
        def respond(request,payload):
            if 'decisions' in payload:
                return dict(coverage_sufficient=False,coverage_reason='比較に必要',coverage_issues=[{'id':'q1','reason':'必要'}])
            return self.unknown()
        result,_=self.run_flow(respond)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(result['items'][0]['basis'],'omitted')
        self.assertTrue(validate_matrix(result,self.plan,self.pages))

    def test_bad_source_id_cannot_be_promoted_by_full_source_validation(self):
        with patch.object(tiered,'checked_request',return_value=({**self.answer,'needs_more_sources':False,'items':[
            {**self.answer['items'][0],'evidence':[{'source_ref':'invented','expert_source_ref':'','source_kind':'primary'}]}]},NS(input_tokens=0,output_tokens=0))):
            result,_=focused.review_pending('same-job',1,self.unknown(),self.plan['items'],self.plan,{},self.pages,self.pages,[])
        self.assertFalse(result['items'][0]['verified'])
        self.assertTrue(validate_matrix(result,self.plan,self.pages))

    def test_reread_can_use_saved_third_party_sources_before_any_new_search(self):
        def respond(request,payload):
            if 'decisions' in payload:return self.responder(request,payload)
            if 'candidate_answers' not in payload:return self.unknown()
            if not payload['sources_complete']:
                return {**self.unknown(),'needs_more_sources':True}
            return {**self.answer,'needs_more_sources':False}
        result,_=self.run_flow(respond)
        self.assertIn('tiered_focused_1_reread_expanded',self.saved)
        self.assertEqual(len(self.calls),5)
        self.assertTrue(result['items'][0]['verified'])
        self.assertFalse(validate_matrix(result,self.plan,self.pages))
        self.assertEqual(result['items'][0]['basis'],'corroborated')

    def test_exact_already_paid_legacy_audit_is_used_before_new_routing(self):
        def respond(request,payload):
            if 'decisions' in payload:return self.responder(request,payload)
            return self.answer
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}):
            old,_=self.run_flow(respond)
        result,usage=self.run_flow(respond)
        self.assertEqual(result,old)
        self.assertEqual(usage.input_tokens,0)
        self.assertFalse(any('focused' in s for s in self.saved))

    def test_policy_changes_only_for_opt_in_and_non_tiered_is_unchanged(self):
        new=matrix_policy()
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}):self.assertNotEqual(matrix_policy(),new)
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'astra'}):
            other=matrix_policy()
            with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}):self.assertEqual(matrix_policy(),other)

    def test_new_reread_evidence_is_visible_to_the_independent_auditor(self):
        self.plan['items'][0]['source_requirement']='primary_only'
        original=copy.deepcopy(self.pages)
        original[0]['text']='古い抜粋の条件';original[0]['truncated']=True
        self.pages[0]['text']='新しく見える料金。税込み。'
        good=copy.deepcopy(self.answer)
        good['items'][0].update(basis='primary',evidence=[{'source_ref':'s1','expert_source_ref':'','source_kind':'primary'}])
        seen=[]
        def request(job,step,req):
            payload=json.loads(req['messages'][0]['content']);seen.append(payload)
            result=copy.deepcopy(good)
            official=next(p for p in payload['sources'] if p['url']==self.pages[0]['url'])
            ref=next(e['source_ref'] for e in official['excerpts'] if '新しく見える料金' in e['text'])
            result['items'][0]['evidence'][0]['source_ref']=ref
            result['needs_more_sources']=False
            return result,NS(input_tokens=0,output_tokens=0)
        with patch.object(tiered,'checked_request',side_effect=request):
            result,_=focused.review_pending('same-job',1,self.unknown(),self.plan['items'],self.plan,{},self.pages,original,[])
        self.assertEqual(len(seen),2)
        text=''.join(e['text'] for p in seen[1]['sources'] for e in p['excerpts'])
        self.assertIn('古い抜粋の条件',text)
        self.assertIn('新しく見える料金',text)
        self.assertTrue(result['items'][0]['verified'])

    def test_exhausted_existing_ledger_blocks_reread_before_transport(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder}), budget.scope('same-job'):
            with budget.ledger() as ledger:
                ledger['calls'].append({'reserved_usd':budget.LIMIT,'cost_usd':budget.LIMIT,'category':'quality'})
            with patch.object(tiered,'get_optional_artifact',return_value=None), patch('pipeline.openai_review.requests.post') as post:
                with self.assertRaises(ContentQualityError):
                    focused.review_pending('same-job',1,self.unknown(),self.plan['items'],self.plan,{},self.pages,self.pages,[])
                post.assert_not_called()
            with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),1)
