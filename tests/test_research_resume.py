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

class SourceLineageTests(unittest.TestCase):
    def test_cross_service_review_receives_evidence_but_not_other_verdicts(self):
        plan,pages,result=fixture()
        common={**plan['items'][0],'id':'q2','subject':'共通','question':'Aの料金を比較できるか'}
        plan['items'].insert(0,common)
        notes={
            'research_collection_1':{'content_text':'common note','meta':{'subject':'共通','source_urls':[pages[0]['url']]}},
            'research_collection_2':{'content_text':'A note','meta':{'subject':'A','source_urls':[p['url'] for p in pages]}}}
        requests=[];saved={}
        def create(*args,**kw):
            payload=json.loads(kw['messages'][0]['content']);requests.append(payload)
            if 'plan' in payload and payload['plan']['items'][0]['id']=='q1':return message(result)
            if 'sources' in payload:
                failed=copy.deepcopy(result);failed['items'][0].update(id='q2',basis='unresolved',status='unresearched')
                return message(failed)
            return message({'coverage_sufficient':False,'coverage_reason':'not confirmed'})
        with patch.object(r,'get_optional_artifact',side_effect=lambda j,s:notes.get(s)), \
             patch.object(r,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:kw})), \
             patch.object(r,'create_with_retry',side_effect=create):
            value,_=r.audit_matrix('j',None,plan,pages,'facts','model',100)
        self.assertEqual(requests[0]['plan']['items'][0]['id'],'q1')
        self.assertEqual(requests[1]['plan']['items'][0]['id'],'q2')
        self.assertEqual({p['url'] for p in requests[1]['sources']},{p['url'] for p in pages})
        self.assertNotIn('decisions',requests[1])
        self.assertFalse(next(i for i in value['items'] if i['id']=='q2')['verified'])
        self.assertEqual(saved['research_check_1']['meta']['subject'],'共通')
        self.assertEqual(saved['research_check_2']['meta']['subject'],'A')

    def test_fetched_linked_price_is_kept_even_if_latest_note_omits_it(self):
        pages=[{'url':'https://official.example','text':'home','links':[{'url':'https://official.example/price'}, {'url':'https://official.example/unfetched'}, {'url':'https://other.example/ad'}]},
               {'url':'https://official.example/price','text':'price','links':[]},
               {'url':'https://other.example/ad','text':'unrelated','links':[]}]
        selected=r.subject_sources(pages,{'https://official.example'})
        self.assertEqual([p['url'] for p in selected],['https://official.example','https://official.example/price'])

    def test_prior_matrix_supplies_source_urls_not_prior_pass(self):
        plan,pages,result=fixture()
        record={'content_text':'new note','meta':{'subject':'A','source_urls':[pages[0]['url']]}}
        prior={'content_text':json.dumps({'items':[{'id':'q1','verified':True,'evidence':[{'url':pages[1]['url']}]}]})}
        outputs=[message(result),message({'coverage_sufficient':False,'coverage_reason':'still missing'})]
        with patch.object(r,'get_optional_artifact',side_effect=lambda j,s:record if s=='research_collection_1' else prior if s=='research_matrix_1' else None), \
             patch.object(r,'upsert_artifact'),patch.object(r,'create_with_retry',side_effect=outputs) as call:
            value,_=r.audit_matrix('j',None,plan,pages,'facts','model',100)
        payload=json.loads(call.call_args_list[0].kwargs['messages'][0]['content'])
        self.assertIn(pages[1]['url'],[p['url'] for p in payload['sources']])
        self.assertFalse(value['coverage_sufficient'])
        self.assertEqual(call.call_count,2)

    def test_retry_keeps_previous_batch_source_lineage(self):
        from pipeline import research_collection as c
        from pipeline.fresh_sources import FreshSources
        from pipeline.content_quality import digest
        plan,pages,_=fixture();fresh=FreshSources({},[])
        fresh.pages={p['url']:p for p in pages}
        old={'content_text':'old note','meta':{'subject':'A','plan_sha256':digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
             'source_urls':[pages[0]['url']]}}
        saved={}
        with patch.object(c,'get_optional_artifact',return_value=old),patch.object(c,'upsert_artifact',side_effect=lambda **kw:saved.update(kw)), \
             patch.object(fresh,'save'),patch.object(c,'run_with_fetch',return_value=(message({}),'new note',[],[])):
            c.collect('j',None,plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},gaps=json.dumps([{'id':'q1'}]))
        self.assertIn(pages[0]['url'],saved['meta']['source_urls'])

    def test_markdown_code_delimiters_are_not_part_of_urls(self):
        from pipeline.fresh_sources import extract_urls
        self.assertEqual(extract_urls('出典：`https://official.example/price`'),['https://official.example/price'])
