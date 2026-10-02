import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
from pipeline import research_search as search, quality_budget as budget
from pipeline.fresh_sources import FreshSources, run_with_fetch
from pipeline.content_quality import ContentQualityError

class ResearchSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'SERPER_API_KEY':'test','QUALITY_BUDGET_DIR':self.tmp.name,'QUALITY_COMPLETION_EVAL':'0'})
        self.env.start();self.ctx=budget.scope('test','research_completeness');self.ctx.__enter__()
        self.records={}
        self.get=patch.object(search,'get_optional_artifact',side_effect=lambda j,s:self.records.get(s));self.get.start()
        self.save=patch.object(search,'upsert_artifact',side_effect=lambda **kw:self.records.update({kw['step']:copy.deepcopy(kw)}));self.save.start()
    def tearDown(self):
        self.save.stop();self.get.stop();self.ctx.__exit__(None,None,None);self.env.stop();self.tmp.cleanup()
    def test_search_cap_resume_and_blocked_urls(self):
        fresh=FreshSources({'never_reference_urls':'https://blocked.example'},[])
        raw={'organic':[{'link':'https://blocked.example','title':'blocked'},{'link':'https://ok.example','title':'good','snippet':'x'*10000}]}
        response=Mock(ok=True);response.json.return_value=raw
        with patch.object(search.requests,'post',return_value=response) as post:
            handler=search.SourceSearch('test','subject',fresh)
            result=handler('query1')
            self.assertEqual(len(result['results']),1)
            self.assertEqual(len(result['results'][0]['snippet']),350)
            self.assertFalse(result['evidence'])
            search.SourceSearch('test','subject',fresh)('query1')
            self.assertEqual(post.call_count,1)
            handler('query2');handler('query3')
            self.assertEqual(handler('query4')['status'],'failed')
            self.assertEqual(post.call_count,3)
        with budget.ledger() as value:
            self.assertEqual(len(value['calls']),3)
            self.assertAlmostEqual(sum(c['reserved_usd'] for c in value['calls']),.003)
    def test_failure_not_rebilled_and_no_key_fallback(self):
        with patch.object(search.requests,'post',side_effect=search.requests.Timeout('secret')) as post:
            handler=search.SourceSearch('test','subject',FreshSources({},[]))
            self.assertNotIn('secret',json.dumps(handler('q')))
            search.SourceSearch('test','subject',FreshSources({},[]))('q')
            self.assertEqual(post.call_count,1)
        with patch.dict(os.environ,{'SERPER_API_KEY':''}), self.assertRaises(ContentQualityError):
            search.SourceSearch('test','subject2',FreshSources({},[]))
    def test_search_is_separate_from_inference_and_snippets_are_not_pages(self):
        fresh=FreshSources({},[])
        block=NS(type='tool_use',name='search_sources',id='one',input={'query':'q'})
        responses=iter([NS(content=[block],stop_reason='tool_use',usage=NS(input_tokens=1,output_tokens=1)),
                       NS(content=[NS(type='text',text='本文未確認')],stop_reason='end_turn',usage=NS(input_tokens=1,output_tokens=1))])
        calls=[]
        def create(*a,**kw):calls.append(copy.deepcopy(kw));return next(responses)
        handler=Mock(return_value={'status':'success','results':[],'evidence':False})
        result=run_with_fetch(None,create=create,model='test',max_tokens=100,system='',prompt='',search_tool=search.SEARCH_TOOL,fresh=fresh,search_handler=handler,max_rounds=4)
        handler.assert_called_once_with('q')
        self.assertEqual(result[2],['q'])
        self.assertFalse(fresh.pages)
        self.assertNotIn('extra_headers',calls[0])
        self.assertFalse(any(t.get('type','').startswith('web_search') for t in calls[0]['tools']))
    def test_search_budget_failure_is_before_http(self):
        with budget.ledger() as value:value['calls']=[{'reserved_usd':1.25,'category':'quality'}]
        with patch.object(search.requests,'post') as post, self.assertRaises(ContentQualityError):
            search.SourceSearch('test','subject',FreshSources({},[]))('q')
        post.assert_not_called()
    def test_usage_settlement_distinguishes_cache_and_preserves_old_rows(self):
        with budget.ledger() as value:value['calls']=[{'cost_usd':.1,'reserved_usd':.1,'category':'quality'}]
        i=budget.reserve_claude({'model':'claude-sonnet-4-6','max_tokens':100,'messages':[]})
        budget.settle(i,{'input_tokens':1000,'cache_creation_input_tokens':3000,'cache_creation':{'ephemeral_5m_input_tokens':2000,'ephemeral_1h_input_tokens':1000},'cache_read_input_tokens':10000,'output_tokens':500})
        with budget.ledger() as value:
            self.assertEqual(value['calls'][0]['cost_usd'],.1)
            self.assertAlmostEqual(value['calls'][1]['cost_usd'],.027)
    def test_tiered_collection_uses_bounded_search_in_normal_entrypoint(self):
        from pipeline import research_collection as collection
        from test_evidence_policy import fixture
        plan,pages,_=fixture();fresh=FreshSources({},[])
        fresh.pages={p['url']:p for p in pages}
        note='資料なし [hypothesis]'
        response=NS(usage=NS(input_tokens=1,output_tokens=1))
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}), \
             patch.object(collection,'get_optional_artifact',return_value=None), \
             patch.object(collection,'upsert_artifact'),patch.object(fresh,'save'), \
             patch.object(collection,'run_with_fetch',return_value=(response,note,[],[])) as run:
            collection.collect('test',None,plan=plan,fresh=fresh,system='policy',prompt='query',model='claude-sonnet-4-6',search_tool={'type':'web_search_20250305'})
            args=run.call_args.kwargs
            self.assertEqual(args['search_tool']['name'],'search_sources')
            self.assertEqual(args['max_rounds'],4)
            self.assertEqual(args['max_tokens'],6000)
            self.assertIsInstance(args['search_handler'],search.SourceSearch)
