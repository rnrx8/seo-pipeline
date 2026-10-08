import json,os,tempfile,unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
from pipeline import ai,quality_budget as budget,step_article
from pipeline.tiered_research import checked_request

ENV={'ARTICLE_WRITING_FLOW':'sonnet_opus','ARTICLE_REVIEW_PROVIDER':'tiered','ARTICLE_GENERATION_MODEL':'claude-opus-5-5'}

class HybridWritingTests(unittest.TestCase):
    def test_repair_output_budget_counts_only_targets_and_keeps_small_patch_default(self):
        from pipeline.step_content_audit import repair_output_limit
        count=Mock(return_value=5000)
        data={'target_block_ids':['a'],'article_blocks':[{'id':'a','text':'短文。'},{'id':'b','text':'隣接'*10000}]}
        self.assertEqual(repair_output_limit(data,6000,count),6000)
        count.assert_not_called()
        data['article_blocks'][0]['text']='修正対象'*1000
        self.assertEqual(repair_output_limit(data,6000,count),14250)
        self.assertEqual([e['id'] for e in json.loads(count.call_args.args[0])['edits']],['a'])
        # Observed failed run: 4355-token replacement sample was assigned 7444
        # total tokens, exhausted by thinking plus an unfinished JSON patch.
        self.assertEqual(repair_output_limit(data,6000,lambda _:4355),13444)
        from pipeline.content_quality import ContentQualityError
        with self.assertRaises(ContentQualityError):repair_output_limit(data,6000,lambda _:None)

    def test_incomplete_response_is_saved_but_not_cached_or_applied(self):
        from contextlib import nullcontext
        from pipeline.content_quality import ContentQualityError
        msg=self.response();msg.stop_reason='max_tokens'
        msg.content.append(NS(type='thinking',text='private reasoning'))
        with patch('pipeline.tiered_research.cached_result',return_value=None), \
             patch('pipeline.quality_budget.request_scope',return_value=nullcontext()), \
             patch('pipeline.tiered_research.create_with_retry',return_value=msg), \
             patch('pipeline.tiered_research.upsert_artifact') as save:
            with self.assertRaises(ContentQualityError):checked_request('job','repair',{'model':'claude-opus-5-5','max_tokens':6000})
        self.assertEqual(save.call_count,1)
        saved=save.call_args.kwargs
        self.assertEqual(saved['step'],'repair_incomplete')
        self.assertEqual(saved['meta']['stop_reason'],'max_tokens')
        self.assertNotIn('private reasoning',saved['content_text'])

    def test_routes_only_draft_and_repair_and_rejects_unsupported_mode(self):
        with patch.dict(os.environ,ENV):
            self.assertEqual(ai.get_step_config('article'),('claude-sonnet-5-5',16000))
            self.assertEqual(ai.get_step_config('content_repair'),('claude-opus-5-5',6000))
            for step in ['outline','search_intent']:self.assertEqual(ai.get_step_config(step)[0],'claude-opus-5-5')
            self.assertEqual(ai.get_step_config('content_audit')[0],'gpt-6.1-sol')
            with patch.dict(os.environ,{'ARTICLE_WRITING_FLOW':'legacy'}):
                self.assertEqual(ai.get_step_config('article')[0],'claude-opus-5-5')
                self.assertEqual(ai.get_step_config('content_repair')[0],'gpt-6.1-sol')
            with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'astra'}):
                with self.assertRaises(ValueError):ai.get_step_config('article')

    def response(self):
        return NS(stop_reason='end_turn',content=[NS(type='text',text='{"edits":[{"id":"block-0002","new":"修正"}]}')],usage=NS(input_tokens=1000,output_tokens=1000,model_dump=lambda:{'input_tokens':1000,'output_tokens':1000}))

    def test_provider_dispatch_budget_and_failed_request_settlement(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{**ENV,'QUALITY_BUDGET_DIR':folder}),budget.scope('hybrid','article'):
            client=Mock();client.with_options.return_value=client
            client.messages.stream.return_value.__enter__=Mock(return_value=NS(get_final_message=lambda:self.response()))
            client.messages.stream.return_value.__exit__=Mock(return_value=False)
            step_article._call(client,[{'role':'user','content':'fixture'}],max_tokens=1200)
            payload=client.messages.stream.call_args.kwargs
            self.assertEqual(payload['model'],'claude-sonnet-5-5');self.assertEqual(payload['max_tokens'],1200)
            with budget.ledger() as ledger:self.assertAlmostEqual(ledger['calls'][0]['cost_usd'],.012)
            with patch('pipeline.tiered_research.cached_result',return_value=None),patch('pipeline.tiered_research.get_optional_artifact',return_value=None),patch('pipeline.tiered_research.upsert_artifact'):
                checked_request('hybrid','tiered_content_repair_1_0',{'model':'claude-opus-5-5','max_tokens':1200,'output_budget_includes_thinking':True,'system':'fixture','messages':[{'role':'user','content':'{}'}]},client=client)
            payload=client.messages.stream.call_args.kwargs
            self.assertEqual(payload['max_tokens'],1200);self.assertNotIn('output_budget_includes_thinking',payload)
            with budget.ledger() as ledger:
                self.assertEqual(len(ledger['calls']),2);self.assertAlmostEqual(ledger['calls'][1]['cost_usd'],.024)
            client.messages.stream.side_effect=TimeoutError('fixture')
            with self.assertRaises(TimeoutError):ai.create_with_retry(client,model='claude-opus-5-5',max_tokens=1200,output_budget_includes_thinking=True,messages=[])
            with budget.ledger() as ledger:self.assertNotEqual(ledger['calls'][-1]['status'],'accounted')
            self.assertEqual(client.messages.stream.call_count,3)

    def test_cached_repair_does_not_construct_client_or_spend(self):
        with patch.dict(os.environ,ENV),patch('pipeline.tiered_research.cached_result',return_value={'edits':[]}),patch('pipeline.ai.anthropic.Anthropic') as construct:
            value,_=checked_request('job','tiered_content_repair_1_0',{'model':'claude-opus-5-5'})
            self.assertEqual(value,{'edits':[]});construct.assert_not_called()

    def test_normal_audit_uses_opus_once_then_reaudits_same_candidate(self):
        from contextlib import ExitStack
        from pipeline import step_content_audit as step
        text='# 記事\n\n## 比較\n\n元の説明。'
        artifacts={'article':{'content_text':text},'outline':{'content_text':'### H2：比較'},'fact_sheet':{'content_text':''},'fresh_sources':{'content_text':json.dumps([{'url':'https://example.test','text':'資料。','status':'success'}])},'content_contract':{'content_text':'{"required_sections":[]}'}}
        bad={'valid':False,'snapshot':'fixture','checks':[{'key':'prose_quality','status':'fail','reason':'fixture','affected_blocks':[{'id':'block-0004','reason':'fixture'}]}]}
        good={'valid':True,'snapshot':'fixture','checks':[{'key':'prose_quality','status':'pass','reason':'fixture','affected_blocks':[]}]}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{**ENV,'QUALITY_RESEARCH_ROUTING':'focused'}))
            stack.enter_context(patch.object(step,'get_artifact',side_effect=lambda _,s:artifacts[s]))
            stack.enter_context(patch.object(step,'get_job',return_value={}))
            stack.enter_context(patch.object(step,'final_review_requirements',return_value={}))
            stack.enter_context(patch.object(step,'failed_audit_checkpoint',return_value=None))
            stack.enter_context(patch.object(step,'upsert_artifact',side_effect=lambda **kw:kw))
            for name in ['validate_delivery','validate_structure','cta_placement_issues']:stack.enter_context(patch.object(step,name,return_value=[]))
            client=stack.enter_context(patch.object(step.anthropic,'Anthropic')).return_value
            audit=stack.enter_context(patch.object(step,'audit',side_effect=[bad,good]))
            repair=stack.enter_context(patch('pipeline.tiered_research.checked_request',return_value=({'edits':[{'id':'block-0004','new':'修正した説明。'}]},None)))
            step.run('fixture','比較',api_key='fixture-key')
            repair.assert_called_once();request=repair.call_args.args[2]
            self.assertEqual(request['model'],'claude-opus-5-5');self.assertEqual(request['max_tokens'],6000);self.assertTrue(request['output_budget_includes_thinking'])
            self.assertIs(repair.call_args.kwargs['client'],client)
            self.assertEqual(json.loads(request['messages'][0]['content'])['target_block_ids'],['block-0004'])
            self.assertEqual(audit.call_count,2);self.assertIn('修正した説明。',audit.call_args.kwargs['text'])
