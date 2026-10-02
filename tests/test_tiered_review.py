import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from pipeline import quality_budget as budget
from pipeline.ai import create_with_retry, get_step_config, validate_model_credentials
from pipeline.content_quality import ContentQualityError
from pipeline.openai_review import create_review_response
from pipeline.research_requirements import validate_matrix, matrix_policy
from pipeline.tiered_research import checked_request, needs_adjudication, validate_visible_matrix
from test_evidence_policy import fixture


class TieredReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_BUDGET_DIR':self.tmp.name,'OPENAI_API_KEY':'test-only'})
        self.env.start();self.ctx=budget.scope('test-job');self.ctx.__enter__()
    def tearDown(self):
        self.ctx.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
    def ledger(self):
        return json.loads(next(Path(self.tmp.name).glob('*.json')).read_text())
    def request(self,model='gpt-6-luna'):
        return dict(model=model,max_tokens=1000,system='audit',messages=[{'role':'user','content':'test'}],output_config={'format':{'schema':{'type':'object'}}})
    def test_routing_and_default_isolation(self):
        self.assertEqual(get_step_config('content_audit')[0],'gpt-6.1-sol')
        self.assertEqual(get_step_config('article')[0],'claude-opus-5-5')
        tiered=matrix_policy()
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'astra'}):
            self.assertEqual(get_step_config('content_audit')[0],'gpt-6-astra')
            self.assertNotEqual(matrix_policy(),tiered)
        for model in budget.RATES:
            with patch('pipeline.openai_review.create_review_response',return_value='ok') as call:
                self.assertEqual(create_with_retry(None,**self.request(model)),'ok')
                self.assertEqual(call.call_args.kwargs['model'],model)
    def test_missing_budget_setting_stops_before_generation(self):
        with patch.dict(os.environ,{'QUALITY_BUDGET_DIR':''}):
            with self.assertRaises(ContentQualityError):validate_model_credentials({})

    def test_generation_costs_do_not_consume_quality_budget_but_corrections_do(self):
        payload={'model':'claude-opus-5-5','max_tokens':24000,'messages':[]}
        with budget.scope('test-job','article'):
            i=budget.reserve_claude(payload)
            budget.settle(i,{'input_tokens':1000,'cache_creation_input_tokens':100,'cache_read_input_tokens':200,'output_tokens':500})
        row=self.ledger()['calls'][0]
        self.assertEqual(row['category'],'generation')
        self.assertAlmostEqual(row['cost_usd'],.0204)
        with budget.scope('test-job','research_validation'):
            i=budget.reserve_claude(payload);budget.settle(i)
        self.assertEqual(self.ledger()['calls'][1]['category'],'quality')
        self.assertEqual(self.ledger()['calls'][1]['status'],'unknown_cost_reserved')

    def test_corrective_search_worst_case_cannot_exceed_cap_silently(self):
        with budget.scope('test-job','research_completeness'):
            with self.assertRaises(ContentQualityError):
                budget.reserve_claude({'model':'claude-sonnet-4-6','max_tokens':9000,
                    'tools':[{'type':'web_search_20250305','max_uses':8}]})
        self.assertEqual(self.ledger()['calls'],[])

    def test_claude_failure_is_recorded_without_sdk_retry(self):
        client=Mock();client.with_options.return_value.messages.stream.side_effect=RuntimeError('failure')
        with budget.scope('test-job','article'):
            with self.assertRaises(RuntimeError):create_with_retry(client,model='claude-opus-5-5',max_tokens=100,messages=[])
        client.with_options.assert_called_once_with(max_retries=0)
        self.assertEqual(self.ledger()['calls'][0]['status'],'unknown_cost_reserved')

    def test_actual_usage_includes_reasoning_and_long_context(self):
        i=budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':1000})
        budget.settle(i,{'input_tokens':280000,'output_tokens':2000,'output_tokens_details':{'reasoning_tokens':1000}})
        self.assertAlmostEqual(self.ledger()['calls'][0]['cost_usd'],1.43)
    def test_failure_reservation_survives_resume_and_prevents_request(self):
        payload={'model':'gpt-6.1-sol','max_output_tokens':110000}
        i=budget.reserve(payload);budget.settle(i)
        with budget.scope('test-job'), patch('pipeline.openai_review.requests.post') as post:
            with self.assertRaises(ContentQualityError):create_review_response(**{**self.request('gpt-6.1-sol'),'max_tokens':20000})
            post.assert_not_called()
        self.assertEqual(len(self.ledger()['calls']),1)
        self.assertEqual(self.ledger()['calls'][0]['status'],'unknown_cost_reserved')
    def test_tiered_rate_limit_does_not_retry_or_hide_expense(self):
        response=Mock(status_code=429);response.json.return_value={'error':{'code':'rate_limit_exceeded'}}
        with patch('pipeline.openai_review.requests.post',return_value=response) as post:
            with self.assertRaises(ContentQualityError):create_review_response(**self.request())
        self.assertEqual(post.call_count,1)
        self.assertEqual(self.ledger()['calls'][0]['status'],'unknown_cost_reserved')
    def test_model_settings_and_success_usage(self):
        for model,effort in [('gpt-6-luna','low'),('gpt-6.1-sol','medium')]:
            data={'status':'completed','usage':{'input_tokens':100,'output_tokens':30},'output':[{'type':'message','content':[{'type':'output_text','text':'{}'}]}]}
            response=Mock(status_code=200)
            response.iter_lines.return_value=[('data: '+json.dumps({'type':'response.completed','response':data})).encode(),b'']
            with patch('pipeline.openai_review.requests.post',return_value=response) as post:
                result=create_review_response(**self.request(model))
            payload=post.call_args.kwargs['json']
            self.assertEqual(payload['max_output_tokens'],1000)
            self.assertEqual(payload['reasoning']['effort'],effort)
            self.assertEqual(payload['service_tier'],'default')
            self.assertEqual(result.usage.output_tokens,30)
        self.assertTrue(all(c['status']=='accounted' for c in self.ledger()['calls']))
    def test_unseen_quote_cannot_be_promoted_by_later_full_body_check(self):
        plan,pages,value=fixture()
        excerpt=copy.deepcopy(pages);excerpt[1]['text']='本文抜粋'
        validate_visible_matrix(value,plan,excerpt)
        self.assertFalse(value['items'][0]['verified'])
        self.assertTrue(validate_matrix(value,plan,pages))
        self.assertFalse(value['items'][0]['verified'])
    def test_complex_and_failed_assertions_are_escalated(self):
        q={'id':'simple','source_requirement':'standard','question':'料金'}
        self.assertTrue(needs_adjudication({'verified':False,'basis':'primary'},q))
        self.assertTrue(needs_adjudication({'verified':True,'basis':'corroborated'},q))
        self.assertTrue(needs_adjudication({'verified':True,'basis':'primary'},{**q,'question':'最安か'}))
        self.assertTrue(needs_adjudication({'verified':True,'basis':'primary'},{**q,'source_requirement':'primary_only'}))
    def test_exact_cache_reused_but_changed_request_or_corrupted_result_not(self):
        saved={};calls=[]
        def save(**kw):saved[kw['step']]=kw
        def response(*args,**kw):
            calls.append(kw);return NS(stop_reason='end_turn',content=[NS(type='text',text='{"ok":true}')],usage=NS(input_tokens=1,output_tokens=1))
        with patch('pipeline.tiered_research.get_optional_artifact',side_effect=lambda j,s:saved.get(s)),patch('pipeline.tiered_research.upsert_artifact',side_effect=save),patch('pipeline.tiered_research.create_with_retry',side_effect=response):
            request=self.request();checked_request('test-job','check',request)
            _,usage=checked_request('test-job','check',request)
            self.assertEqual(len(calls),1);self.assertEqual(usage.input_tokens,0)
            changed={**request,'system':'new rules'};checked_request('test-job','check',changed)
            self.assertEqual(len(calls),2)
            saved['check']['content_text']='{"ok":false}'
            checked_request('test-job','check',changed);self.assertEqual(len(calls),3)

    def test_normal_matrix_dispatches_only_unresolved_to_sol_and_reuses_all_checkpoints(self):
        from pipeline import tiered_research as tiered
        plan,pages,value=fixture()
        item=value['items'][0]
        item.update(basis='primary',evidence=[{'url':pages[1]['url'],'quote':pages[1]['text'],'source_kind':'primary'}])
        # Pick a non-sampled supported claim, so only the unresolved one escalates.
        q=plan['items'][0]
        q['id']=next('simple'+str(i) for i in range(100) if not needs_adjudication({'verified':True,'basis':'primary'},{**q,'id':'simple'+str(i)}))
        item['id']=q['id']
        plan['items'].append({**q,'id':'unresolved'})
        answer={**copy.deepcopy(item),'id':'unresolved'}
        value['items'].append({**copy.deepcopy(answer),'status':'unresearched','basis':'unresolved'})
        saved={};calls=[]
        def generate(*args,**request):
            calls.append(request)
            payload=json.loads(request['messages'][0]['content'])
            if request['model']=='gpt-6-luna':result=value
            elif 'decisions' in payload:result={'coverage_sufficient':True,'coverage_reason':'主要項目を確認','coverage_issues':[]}
            else:
                self.assertEqual([q['id'] for q in payload['plan']['items']],['unresolved'])
                result={'items':[answer],'coverage_sufficient':True,'coverage_reason':'確認済み'}
            result=copy.deepcopy(result)
            for item in result.get('items',[]):
                for ref in item['evidence']:
                    page=next(p for p in payload['sources'] if p['url']==ref['url'])
                    ref['source_ref']=next(e['source_ref'] for e in page['excerpts'] if ref['quote'] in e['text'])
                    ref.pop('quote');ref['expert_source_ref']='';ref.pop('expert_qualification_quote',None)
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(result))],usage=NS(input_tokens=10,output_tokens=10))
        with patch.object(tiered,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))), \
             patch.object(tiered,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:copy.deepcopy(kw)})), \
             patch.object(tiered,'create_with_retry',side_effect=generate):
            result,_=tiered.audit_matrix('test-job',plan,pages,'facts',{})
            self.assertTrue(all(i['verified'] for i in result['items']))
            self.assertFalse(validate_matrix(result,plan,pages))
            again,usage=tiered.audit_matrix('test-job',plan,pages,'facts',{})
        self.assertEqual([c['model'] for c in calls],['gpt-6-luna','gpt-6.1-sol','gpt-6.1-sol'])
        self.assertEqual(usage.input_tokens,0)
        self.assertEqual(result,again)


if __name__=='__main__':unittest.main()
