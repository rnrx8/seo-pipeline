import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_evidence_policy import fixture
from pipeline import research_verification as r


def message(value):
    return SimpleNamespace(stop_reason="end_turn",content=[SimpleNamespace(text=json.dumps(value))],
                           usage=SimpleNamespace(input_tokens=3,output_tokens=4))


class ResearchResumeTests(unittest.TestCase):
    def test_failed_matrix_resumes_retrieval_then_requires_new_full_audit(self):
        from pipeline import research_requirements as req
        plan,pages,result=fixture()
        failed={'valid':False,'attempt':1,'gaps':[{'id':'q1','reason':'不足'}],
            'policy_sha256':req.matrix_policy(),
            'plan_sha256':req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True))}
        artifact={'content_text':json.dumps(failed)}
        self.assertEqual(req.resume_research_gaps(artifact,plan)[0],1)
        for change in ({'valid':True},{'policy_sha256':'old'},{'plan_sha256':'old'}):
            self.assertIsNone(req.resume_research_gaps({'content_text':json.dumps({**failed,**change})},plan))
        events=[]
        artifacts={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        def audit(*a,**kw):
            events.append('audit');return result,SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch.object(req,'load_plan',return_value=plan),patch.object(req,'get_optional_artifact',side_effect=lambda j,s:artifact if s=='research_matrix' else None), \
             patch.object(req,'matrix_policy',return_value=failed['policy_sha256']),patch.object(req,'refresh_dynamic_sources'),patch.object(req,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:kw),patch.object(req,'get_step_config',return_value=('gpt-6.1-sol',100)), \
             patch('pipeline.step_fact_sheet.run',side_effect=lambda *a,**kw:events.append('retrieve')),patch.object(r,'audit_matrix',side_effect=audit):
            saved=req.verify('j','query')
        self.assertEqual(events,['retrieve','audit'])
        self.assertTrue(saved['meta']['valid'])
        self.assertEqual(json.loads(saved['content_text'])['attempt'],2)

    def test_policy_change_does_not_reset_completed_supplement(self):
        from pipeline import research_requirements as req
        plan,pages,result=fixture()
        result['items'][0].update(status='unresearched',basis='unresolved')
        records={'research_matrix':{'content_text':json.dumps({'valid':False,'attempt':1,'policy_sha256':'old'})},
                 'research_supplement':{'content_text':json.dumps({'status':'completed','request_sha256':'old'})}}
        artifacts={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        with patch.object(req,'load_plan',return_value=plan), \
             patch.object(req,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(req,'refresh_dynamic_sources'), \
             patch.object(req,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:records.update({kw['step']:kw}) or kw), \
             patch.object(req,'get_step_config',return_value=('gpt-6.1-sol',100)), \
             patch.object(req,'supplement_once') as supplement, \
             patch.object(r,'audit_matrix',return_value=(result,SimpleNamespace(input_tokens=0,output_tokens=0))) as audit:
            with self.assertRaises(req.ContentQualityError):req.verify('j','query')
        supplement.assert_not_called()
        self.assertEqual(audit.call_count,1)
        self.assertEqual(json.loads(records['research_matrix']['content_text'])['attempt'],2)
        self.assertFalse(records['research_matrix']['meta']['valid'])

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

    def test_coverage_feedback_rechecks_saved_sources_without_changing_other_requests(self):
        issue={'id':'q1','reason':'Use the supported part of the existing answer'}
        def add_feedback(plan,pages,saved):
            saved['research_matrix_1']={'content_text':json.dumps({'coverage_issues':[issue]})}
        calls,_,_=self.run_case(add_feedback)
        self.assertEqual(len(calls),4)
        original=json.loads(calls[0]['messages'][0]['content'])
        recheck=json.loads(calls[2]['messages'][0]['content'])
        self.assertNotIn('previous_findings',original)
        self.assertEqual(recheck.pop('previous_findings'),[issue])
        self.assertEqual(recheck,original)

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
             patch.object(req.anthropic,'Anthropic'),patch.object(req,'create_with_retry',return_value=message({'updates':[],'additions':[],'candidate_services':['A'],'scope_reason':'対象A'})), \
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

    def test_tiered_policy_repair_uses_budgeted_sol_not_hidden_opus(self):
        from pipeline import research_requirements as req
        from pipeline.ai import get_step_config
        calls=[]
        def record(*args,**kwargs):
            calls.append(kwargs)
            return message({'updates':[],'additions':[],'candidate_services':['A'],'scope_reason':'対象A'})
        plan,_,_=fixture()
        usage=SimpleNamespace(input_tokens=1,output_tokens=1)
        verdicts=[({'valid':False,'issues':[{'id':'q1','reason':'修正'}]},usage),({'valid':True,'issues':[]},usage)]
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}), \
             patch.object(req,'get_artifact',return_value={'content_text':json.dumps(plan),'meta':{}}), \
             patch.object(req,'get_optional_artifact',return_value=None),patch.object(req,'_plan_context',return_value={}), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:kw),patch.object(req,'create_with_retry',side_effect=record), \
             patch('pipeline.research_plan_review.review',side_effect=verdicts):
            req.revalidate_plan('j','keyword')
        self.assertEqual(calls[0]['model'],'gpt-6.1-sol')
        self.assertIn('scope_reason',calls[0]['output_config']['format']['schema']['required'])

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
             patch.object(req.anthropic,'Anthropic'),patch.object(req,'create_with_retry',return_value=message({'updates':[update],'additions':[addition],'candidate_services':['A'],'scope_reason':'対象A'})) as generate, \
             patch('pipeline.research_plan_review.review',side_effect=verdicts) as review:
            req.revalidate_plan('j','keyword')
        value=json.loads(saved['research_plan']['content_text'])
        self.assertEqual([i['id'] for i in value['items']],['q1','q2'])
        self.assertTrue(value['items'][0]['required']);self.assertFalse(value['items'][1]['required'])
        self.assertNotIn('research_collection_1',saved)
        self.assertEqual(generate.call_count,1);self.assertEqual(review.call_count,2)

class PlanScopeRepairTests(unittest.TestCase):
    def test_candidate_and_scope_change_with_questions_without_mutating_original(self):
        from pipeline.research_requirements import apply_plan_repair
        plan,_,_=fixture();plan.update(candidate_services=['A'],scope_reason='対象A')
        before=copy.deepcopy(plan)
        addition={**plan['items'][0],'id':'q2','subject':'B'}
        result=apply_plan_repair(plan,{'updates':[],'additions':[addition],
            'candidate_services':['A','B'],'scope_reason':'上位複数記事の重要対象としてAとBを比較'})
        self.assertEqual(result['candidate_services'],['A','B'])
        self.assertIn('AとB',result['scope_reason'])
        self.assertEqual(result['items'][-1]['subject'],'B')
        self.assertEqual(plan,before)

    def test_non_comparison_article_can_keep_empty_candidate_list(self):
        from pipeline.research_requirements import apply_plan_repair
        plan,_,_=fixture();plan.update(candidate_services=[],scope_reason='一般的な解説')
        result=apply_plan_repair(plan,{'updates':[],'additions':[],
            'candidate_services':[],'scope_reason':'一般的な解説'})
        self.assertEqual(result['candidate_services'],[])

    def test_missing_scope_or_duplicate_added_ids_cannot_partially_apply(self):
        from pipeline.research_requirements import apply_plan_repair
        plan,_,_=fixture();before=copy.deepcopy(plan)
        addition={**plan['items'][0],'id':'q2','subject':'B'}
        for changes in [
            {'updates':[],'additions':[addition]},
            {'updates':[],'additions':[addition,addition],'candidate_services':['A','B'],'scope_reason':'理由'},
            {'updates':[],'additions':[addition],'candidate_services':['A','A'],'scope_reason':'理由'}]:
            with self.assertRaises(ValueError):apply_plan_repair(plan,changes)
            self.assertEqual(plan,before)


class SourceLineageTests(unittest.TestCase):
    def test_supplemental_retrieval_has_bounded_new_capacity(self):
        from pipeline.step_fact_sheet import restore_research_sources
        from pipeline.fresh_sources import FreshSources
        pages=[{'url':f'https://example.org/{n}','status':'success','text':'body','browser_attempted':n<20} for n in range(120)]
        fresh=FreshSources({},[],max_urls=120,max_browser_attempts=20)
        restore_research_sources(fresh,pages)
        self.assertEqual(fresh.max_urls,160)
        self.assertEqual(fresh.browser_attempts,20)
        with patch.object(fresh,'_retrieve',side_effect=lambda u:{'url':u,'status':'success','text':'new'}):
            for n in range(120,160):self.assertEqual(fresh.fetch(f'https://example.org/{n}')['status'],'success')
            self.assertEqual(fresh.fetch('https://example.org/160')['status'],'failed')
            restore_research_sources(fresh,list(fresh.pages.values()))
            self.assertEqual(fresh.max_urls,200)
            for n in range(160,200):fresh.fetch(f'https://example.org/{n}')
            restore_research_sources(fresh,list(fresh.pages.values()))
            self.assertEqual(fresh.max_urls,200)
            self.assertEqual(fresh.fetch('https://example.org/200')['status'],'failed')

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
             'source_urls':[pages[0]['url']],'search_queries':['earlier search']}}
        saved={}
        with patch.object(c,'get_optional_artifact',side_effect=lambda j,s:old if s=='research_collection_1' else None),patch.object(c,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:kw})), \
             patch.object(fresh,'save'),patch.object(c,'run_with_fetch',return_value=(message({}),'new note',['new search'],[])):
            c.collect('j',None,plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},gaps=json.dumps([{'id':'q1'}]))
        self.assertIn(pages[0]['url'],saved['research_collection_1']['meta']['source_urls'])
        self.assertEqual(saved['research_collection_1']['meta']['search_queries'],['earlier search','new search'])

    def test_markdown_code_delimiters_are_not_part_of_urls(self):
        from pipeline.fresh_sources import extract_urls
        self.assertEqual(extract_urls('出典：`https://official.example/price`'),['https://official.example/price'])

class CoverageRetrievalRoutingTests(unittest.TestCase):
    def case(self):
        plan,pages,result=fixture()
        item=result['items'][0]
        item['additional_sources_needed']=False
        result['coverage_issues']=[{'id':item['id'],'reason':'Reconsider the existing answer'}]
        gaps=[{**plan['items'][0],'reason':'coverage disagreement'}]
        return plan,pages,result,gaps

    def test_existing_quote_and_explicit_routing_avoids_retrieval_not_validation(self):
        from pipeline import research_requirements as req
        plan,pages,result,gaps=self.case()
        before=copy.deepcopy(result)
        self.assertEqual(req.retrieval_gaps(gaps,result,pages),[])
        self.assertEqual(result,before)
        result.update(coverage_sufficient=False,coverage_reason='still unresolved')
        self.assertTrue(req.validate_matrix(result,plan,pages))

    def test_missing_sources_or_routing_or_actual_quote_still_requires_retrieval(self):
        from pipeline import research_requirements as req
        for change in (
            lambda r,p:r['items'][0].update(additional_sources_needed=True),
            lambda r,p:r['items'][0].pop('additional_sources_needed'),
            lambda r,p:r.update(coverage_issues=[]),
            lambda r,p:[ref.update(quote='not in any source') for ref in r['items'][0]['evidence']],
            lambda r,p:[page.update(status='failed') for page in p]):
            plan,pages,result,gaps=self.case();change(result,pages)
            self.assertEqual(req.retrieval_gaps(gaps,result,pages),gaps)

    def test_omission_keeps_candidate_evidence_for_recheck_not_publication(self):
        from pipeline import research_requirements as req
        plan,pages,result,gaps=self.case()
        item=result['items'][0]
        item['omission_candidate_from']=copy.deepcopy(item)
        item.update(basis='omitted',answer='',evidence=[])
        self.assertEqual(req.retrieval_gaps(gaps,result,pages),[])
        self.assertEqual(item['answer'],'')

    def test_running_supplement_preserves_identity_and_fetches_only_missing_items(self):
        from pipeline import research_requirements as req
        plan,pages,result,gaps=self.case()
        missing={**gaps[0],'id':'q2'};gaps.append(missing)
        key=req.digest(json.dumps({'plan':plan,'gaps':gaps},ensure_ascii=False,sort_keys=True))
        saved=[]
        with patch.object(req,'get_optional_artifact',return_value={'content_text':json.dumps({'request_sha256':key,'status':'running'})}), \
             patch.object(req,'get_artifact',return_value={'content_text':json.dumps(pages)}), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:saved.append(json.loads(kw['content_text']))), \
             patch('pipeline.step_fact_sheet.run') as collect:
            req.supplement_once('j','query',plan,gaps,matrix=result)
        self.assertEqual(json.loads(collect.call_args.kwargs['research_gaps']),gaps)
        self.assertEqual(collect.call_args.kwargs['research_question_ids'],['q2'])
        self.assertEqual(saved[-1],{'request_sha256':key,'status':'completed'})

    def test_recheck_failure_stops_at_second_audit_without_recollection(self):
        from pipeline import research_requirements as req
        plan,pages,result,gaps=self.case()
        result.update(coverage_sufficient=False,coverage_reason='still unresolved')
        records={};artifacts={'fresh_sources':{'content_text':json.dumps(pages)},'fact_sheet':{'content_text':'facts'}}
        with patch.object(req,'load_plan',return_value=plan), \
             patch.object(req,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(req,'refresh_dynamic_sources'), \
             patch.object(req,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
             patch.object(req,'upsert_artifact',side_effect=lambda **kw:records.update({kw['step']:kw}) or kw), \
             patch.object(req,'get_step_config',return_value=('gpt-6.1-sol',100)), \
             patch('pipeline.step_fact_sheet.run') as collect, \
             patch.object(r,'audit_matrix',side_effect=lambda *a,**k:(copy.deepcopy(result),SimpleNamespace(input_tokens=0,output_tokens=0))) as audit:
            with self.assertRaises(req.ContentQualityError):req.verify('j','query')
            self.assertEqual(audit.call_count,2)
            with self.assertRaises(req.ContentQualityError):req.verify('j','query')
            self.assertEqual(audit.call_count,2)
        collect.assert_not_called()
        self.assertEqual(json.loads(records['research_matrix']['content_text'])['attempt'],2)
        self.assertFalse(records['research_matrix']['meta']['valid'])

    def test_narrowed_work_reuses_paid_receipt_with_original_request_identity(self):
        from pipeline import research_collection as c
        from pipeline.fresh_sources import FreshSources
        plan,pages,result,gaps=self.case()
        plan['items'].append({**plan['items'][0],'id':'q2','subject':'B'})
        gaps.append({**plan['items'][1],'reason':'other coverage issue'})
        fresh=FreshSources({},[]);fresh.pages={p['url']:p for p in pages}
        records={};calls=[]
        def save(**kw):records[kw['step']]=copy.deepcopy(kw)
        def generate(*args,**kw):
            calls.append(kw)
            return message({}),'saved source notes',[],[]
        with patch.object(c,'tiered_review_enabled',return_value=False), \
             patch.object(c,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(c,'upsert_artifact',side_effect=save),patch.object(fresh,'save'), \
             patch.object(c,'run_with_fetch',side_effect=generate):
            args=dict(plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},gaps=json.dumps(gaps))
            c.collect('j',None,**args)
            self.assertEqual(len(calls),2)
            c.collect('j',None,question_ids=['q1'],**args)
            self.assertEqual(len(calls),2)
            self.assertEqual(records['research_collection_2']['content_text'],'saved source notes')
