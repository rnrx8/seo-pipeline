"""Offline behavioral tests for scope, durable limits, and evidence handoff."""
import copy
import json
import os
import socket
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import quality_budget as budget, tiered_research as research
from pipeline import review_scope as scope, tiered_evidence as evidence
from pipeline.content_quality import ContentQualityError, digest
from pipeline.content_edits import content_blocks
from pipeline.focused_quality import ROLES


class ScopedReviewTests(unittest.TestCase):
    def setUp(self):
        self.text='# 比較\n\n## A\n\nAの女性は標準プランでメッセージ無料。\n\n対象条件を確認。\n\n## B\n\nBは12ヶ月一括払いで月額換算100円。\n\n備考。\n\n## 結論\n\nAの女性は無料で会話できる。'
        self.blocks=content_blocks(self.text)
        self.a,self.b,self.summary=(self.blocks[n]['id'] for n in (2,5,8))
        self.pages=[{'url':'https://a.example/price','text':'女性・標準プランは無料。','fetched_at':'2026-10-01'},
                    {'url':'https://b.example/price','text':'12ヶ月一括1200円。','fetched_at':'2026-10-01'},
                    {'url':'https://unrelated.example','text':'unrelated'*1000}]
        self.req={'keyword':'比較','research_plan':{'items':[{'id':'a','subject':'A'},{'id':'b','subject':'B'}]},
                  'research_decisions':{'items':[{'id':'a','answer':'女性は基本無料','evidence_sources':[{'url':self.pages[0]['url']}]},
                                                  {'id':'b','answer':'月額換算100円','evidence_sources':[{'url':self.pages[1]['url']}]}]}}
        self.check={'key':'evidence_support','status':'fail','reason':'標準プランとメッセージの対応',
                    'requires_source_check':True,'research_ids':['a'],'source_urls':[],
                    'affected_blocks':[{'id':self.a,'reason':'無料範囲'},{'id':self.summary,'reason':'同じ主張'}]}
        self.payload={'article_blocks':self.blocks,'requirements':self.req,'source_documents':self.pages,'candidate_checks':[self.check]}
        self.saved={}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ,{'QUALITY_BUDGET_DIR':self.tmp,'ARTICLE_REVIEW_PROVIDER':'tiered',
            'QUALITY_RESEARCH_ROUTING':'focused','QUALITY_COMPLETION_EVAL':'','QUALITY_TOTAL_LIMIT_USD':'10'}))
        self.stack.enter_context(budget.scope('offline-job'))
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(research,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(self.saved.get(s))))
        self.stack.enter_context(patch.object(research,'upsert_artifact',side_effect=lambda **kw:self.saved.update({kw['step']:copy.deepcopy(kw)})))

    def message(self,request,fail=False):
        fields=request['output_config']['format']['schema']['properties']['checks']['items']['properties']
        return {'checks':[{'key':k,'status':'fail' if fail and k=='evidence_support' else 'pass',
            'reason':'女性の標準プランが無料。メッセージを含む。',
            'affected_blocks':[{'id':self.a,'reason':'条件欠落'}] if fail and k=='evidence_support' else []}
            for k in fields['key']['enum']],
            'review':{'needed':False,'block_ids':[],'source_urls':[self.pages[0]['url']],'reason':'原文で対応確認済み'}}

    def fake_provider(self,fail=False):
        def send(_,**request):
            # Exercise the real durable reservation boundary, but no HTTP.
            n=budget.reserve({'model':request['model'],'max_output_tokens':100,'input':'offline fixture'})
            budget.settle(n,{'input_tokens':100,'output_tokens':20})
            result=self.message(request,fail)
            return NS(stop_reason="end_turn",content=[NS(type='text',text=json.dumps(result,ensure_ascii=False))],usage=NS(input_tokens=100,output_tokens=20))
        return send

    def test_scope_keeps_all_occurrences_but_excludes_other_services(self):
        packet=scope.scoped_packet(self.payload,[self.check])
        self.assertEqual(set(packet['target_block_ids']),{self.a,self.summary})
        self.assertEqual(packet['source_documents'],self.pages[:1])
        self.assertEqual([i['id'] for i in packet['requirements']['research_decisions']['items']],['a'])
        self.assertNotIn(self.b,{b['id'] for b in packet['article_blocks']})
        scope.validate_routes([self.check],self.blocks,self.req,self.pages,ROLES['evidence'])

    def test_missing_or_invented_scope_stops_instead_of_full_collection(self):
        for field,value in [('affected_blocks',[]),('source_urls',['https://invented.example']),('research_ids',['invented'])]:
            with self.subTest(field=field):
                with self.assertRaises(ContentQualityError):
                    scope.validate_routes([{**self.check,field:value}],self.blocks,self.req,self.pages,ROLES['evidence'])
        packet=scope.scoped_packet(self.payload,[self.check])
        with self.assertRaises(ContentQualityError):
            scope.validate_edits(json.dumps({'edits':[{'id':self.b,'new':'Aの女性は無料'}]}),packet)

    def test_resolution_survives_unrelated_change_but_not_conditions_or_sources(self):
        with patch.object(research,'create_with_retry',side_effect=self.fake_provider()) as send:
            evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
            checks,usage,trace=evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
            self.assertEqual(send.call_count,1)
            self.assertEqual(usage.input_tokens,0)
            self.assertEqual(trace[0]['phase'],'reused')
        receipts=scope.source_resolutions('research',self.blocks,self.pages,self.req)
        self.assertTrue(receipts)
        self.assertIn('標準プラン',receipts[0]['checks'][0]['reason'])
        changed=copy.deepcopy(self.blocks);changed[5]['text']='Bの条件を変更。'
        self.assertEqual(len(scope.source_resolutions('research',changed,self.pages,self.req)),len(receipts))
        changed=copy.deepcopy(self.blocks);changed[2]['text']='男性も全機能無料。'
        self.assertLess(len(scope.source_resolutions('research',changed,self.pages,self.req)),len(receipts))
        pages=copy.deepcopy(self.pages);pages[0]['fetched_at']='new fetch'
        self.assertFalse(scope.source_resolutions('research',self.blocks,pages,self.req))
        req=copy.deepcopy(self.req);req['research_decisions']['items'][0]['answer']='女性・年契約のみ無料'
        self.assertFalse(scope.source_resolutions('research',self.blocks,self.pages,req))

    def test_failed_resolution_is_not_promoted_to_pass_on_resume(self):
        with patch.object(research,'create_with_retry',side_effect=self.fake_provider(True)) as send:
            first=evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
            second=evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
        self.assertEqual(send.call_count,1)
        self.assertEqual(first[0][0]['status'],'fail')
        self.assertEqual(second[0][0]['status'],'fail')

    def test_durable_limits_apply_to_direct_calls_and_survive_restarts(self):
        step='outline_local_repair_response'
        req={'model':'gpt-6-luna','max_tokens':100,'system':'repair','messages':[{'role':'user','content':'{}'}]}
        def send(_,**kwargs):
            n=budget.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':'repair'})
            budget.settle(n,{'input_tokens':10,'output_tokens':10})
            return NS(stop_reason="end_turn",content=[NS(type='text',text='{"edits":[]}')],usage=NS(input_tokens=10,output_tokens=10))
        with patch.object(research,'create_with_retry',side_effect=send) as call:
            research.checked_request('offline-job',step,req)
            with budget.scope('offline-job','anything'):
                research.checked_request('offline-job',step,req)
                with self.assertRaisesRegex(ContentQualityError,'通算回数'):
                    research.checked_request('offline-job',step,{**req,'system':'another repair'})
            self.assertEqual(call.call_count,2) # second reaches local guard, never network
        with budget.ledger() as ledger:
            self.assertEqual(len(ledger['calls']),1)
            self.assertEqual(ledger['calls'][0]['review_operation']['role'],'repair')

    def test_phase_allocation_reserves_later_stage_budget_even_in_evaluation(self):
        with budget.ledger() as ledger:
            ledger['calls']=[{'reserved_usd':.49,'cost_usd':.49,'review_operation':{'phase':'outline','role':'audit','request_sha256':'before'}}]
            ledger.update(total_limit_usd=10,completion_evaluation={'stop_after_usd':10,'authorization':'test fixture'})
        req={'messages':[{'content':'{}'}]}
        with patch.dict(os.environ,{'QUALITY_COMPLETION_EVAL':'1'}),budget.request_scope('offline-job','outline_local_repair_response','new',req):
            with self.assertRaisesRegex(ContentQualityError,'費用枠'):
                budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':6000,'input':'x'})
        with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),1)

    def test_unknown_prior_charge_blocks_duplicate_and_legacy_is_not_reset(self):
        req={'messages':[{'content':'{}'}]}
        with budget.request_scope('offline-job','tiered_audit_research','same',req):
            n=budget.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':'x'})
            budget.settle(n)
        with budget.request_scope('offline-job','tiered_audit_research','same',req):
            with self.assertRaisesRegex(ContentQualityError,'再送'):
                budget.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':'x'})
        with budget.ledger() as ledger:
            ledger['calls']=[{'stage':'research_validation','reserved_usd':.1,'cost_usd':.1}]
        with budget.request_scope('offline-job','tiered_audit_research','new',req):
            with self.assertRaisesRegex(ContentQualityError,'移行確認'):
                budget.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':'x'})

    def test_two_versions_cache_preserves_older_response(self):
        step='tiered_audit_research'
        req={'model':'gpt-6-luna','max_tokens':100,'system':'one','messages':[{'role':'user','content':'{}'}],
             'output_config':{'format':{'schema':{'properties':{'checks':{'items':{'properties':{'key':{'enum':['evidence_support']}}}}}}}}}
        with patch.object(research,'create_with_retry',side_effect=self.fake_provider()) as send:
            research.checked_request('offline-job',step,req)
            research.checked_request('offline-job',step,{**req,'system':'two'})
            _,usage=research.checked_request('offline-job',step,req)
            self.assertEqual(send.call_count,2)
            self.assertEqual(usage.input_tokens,0)

    def test_comparison_table_links_do_not_pull_in_all_services(self):
        blocks=copy.deepcopy(self.blocks)
        blocks[2]['text']+='\n表の出典：https://b.example/price https://unrelated.example'
        packet=scope.scoped_packet({**self.payload,'article_blocks':blocks},[self.check])
        self.assertEqual(packet['source_documents'],self.pages[:1])
        self.assertEqual([q['id'] for q in packet['requirements']['research_decisions']['items']],['a'])

    def test_final_article_report_keeps_fact_dependencies_for_repair(self):
        from pipeline.focused_quality import audit_article
        def send(job,step,request):
            fields=request['output_config']['format']['schema']['properties']['checks']['items']['properties']
            checks=[]
            for key in fields['key']['enum']:
                c={'key':key,'status':'pass','reason':'fixture','affected_blocks':[]}
                if 'requires_source_check' in fields:
                    c.update(requires_source_check=False,source_urls=[],research_ids=[])
                if key=='evidence_support':
                    c.update(status='fail',research_ids=['a'],affected_blocks=self.check['affected_blocks'],reason='女性条件が欠落')
                checks.append(c)
            return {'checks':checks},NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send):
            report=audit_article(None,text=self.text,facts='',outline='',contract={},requirements=self.req,sources=json.dumps(self.pages))
        self.assertFalse(report['valid'])
        packet=scope.repair_packet(self.text,self.req,self.pages,report)
        self.assertEqual(packet['source_documents'],self.pages[:1])
        self.assertEqual(set(packet['target_block_ids']),{self.a,self.summary})

    def test_normal_outline_repair_recheck_and_restart_use_same_durable_flow(self):
        from pipeline import step_research_guard as guard
        from pipeline import content_quality as cq
        artifacts={'outline':{'content_text':self.text,'meta':{}},'fact_sheet':{'content_text':''},
                   'fresh_sources':{'content_text':json.dumps([{**p,'status':'success'} for p in self.pages])},
                   'content_contract':{'content_text':'{}'}}
        requests=[]
        def write(**kw):
            artifacts[kw['step']]=copy.deepcopy(kw)
            return kw
        def send(_,**request):
            data=json.loads(request['messages'][0]['content']);requests.append(data)
            n=budget.reserve({'model':request['model'],'max_output_tokens':100,'input':'offline fixture'})
            budget.settle(n,{'input_tokens':100,'output_tokens':20})
            if 'edits' in request['output_config']['format']['schema']['properties']:
                self.assertEqual([p['url'] for p in data['source_documents']],[self.pages[0]['url']])
                self.assertEqual(set(data['target_block_ids']),{self.a,self.summary})
                value={'edits':[{'id':b['id'],'new':b['text']+'（女性の標準プラン）'} for b in self.blocks if b['id'] in (self.a,self.summary)]}
            else:
                repaired='（女性の標準プラン）' in artifacts['outline']['content_text']
                value={'checks':[{'key':k,'status':'pass','reason':'offline fixture','affected_blocks':[],
                                  'requires_source_check':False,'research_ids':[],'source_urls':[]} for k in cq.CHECKS]}
                if not repaired:
                    value['checks'][1]={**self.check,'requires_source_check':False}
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(value))],usage=NS(input_tokens=100,output_tokens=20))
        with ExitStack() as stack:
            stack.enter_context(patch.object(guard,'get_job',return_value={'id':'offline-job'}))
            stack.enter_context(patch.object(guard,'get_artifact',side_effect=lambda j,s:copy.deepcopy(artifacts[s])))
            stack.enter_context(patch.object(guard,'upsert_artifact',side_effect=write))
            stack.enter_context(patch.object(guard,'requirements_for',return_value=self.req))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value={}))
            stack.enter_context(patch('pipeline.step_outline.ensure_complete_volume_design',side_effect=lambda t,w:(t,False)))
            stack.enter_context(patch('pipeline.step_structure_guard.validate_structure',return_value=[]))
            stack.enter_context(patch('pipeline.step_structure_guard.run_before_research'))
            stack.enter_context(patch.object(guard.anthropic,'Anthropic'))
            stack.enter_context(patch.object(research,'create_with_retry',side_effect=send))
            result=guard.run('offline-job','比較')
            self.assertTrue(json.loads(result['content_text'])['valid'])
            self.assertEqual(len(requests),3)
            guard.run('offline-job','比較')
            self.assertEqual(len(requests),3)
            self.assertEqual(content_blocks(artifacts['outline']['content_text'])[5]['text'],self.blocks[5]['text'])
        with budget.ledger() as ledger:
            self.assertEqual([c['review_operation']['role'] for c in ledger['calls']],['audit','repair','audit'])

    def test_new_source_scope_after_repair_is_not_silently_purchased(self):
        with budget.ledger() as ledger:
            ledger['calls']=[{'reserved_usd':.001,'cost_usd':.001,
                'review_operation':{'phase':'outline','role':role,'request_sha256':role,
                                    'source_urls':[self.pages[0]['url']],'research_ids':['a']}}
                for role in ('screen','repair')]
        request={'messages':[{'content':json.dumps({'source_documents':self.pages[1:2],
                   'candidate_checks':[{'research_ids':['b'],'reason':'new topic'}]})}]}
        with budget.request_scope('offline-job','tiered_research_evidence_screen','new',request):
            with self.assertRaisesRegex(ContentQualityError,'新しい照合論点'):
                budget.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':'x'})
        with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),2)

    def test_new_question_inside_same_paragraph_cannot_inherit_old_pass(self):
        with patch.object(research,'create_with_retry',side_effect=self.fake_provider()) as send:
            evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
            revised=copy.deepcopy(self.payload)
            revised['candidate_checks'][0]['reason']='同じ料金ページだが、返金条件という別の疑問'
            evidence.evidence_audit('research',revised,ROLES['evidence'],self.text)
            self.assertEqual(send.call_count,2)

    def test_unresolved_comparison_cannot_become_pass_when_evidence_support_was_not_requested(self):
        from pipeline.content_quality import audit, CHECKS
        check={**self.check,'key':'comparison_conditions'}
        def send(job,step,request):
            if step=='tiered_audit_research':
                return {'checks':[check if k=='comparison_conditions' else {'key':k,'status':'pass','reason':'fixture',
                    'affected_blocks':[],'requires_source_check':False,'source_urls':[],'research_ids':[]} for k in CHECKS]},NS(input_tokens=0,output_tokens=0)
            value=self.message(request)
            value['review'].update(needed=True,block_ids=[self.a],reason='条件不明')
            return value,NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send),patch('pipeline.db.get_optional_artifact',return_value=None):
            report=audit(None,stage='research',text=self.text,outline=self.text,facts='',requirements=self.req,sources=json.dumps(self.pages),contract={})
        self.assertTrue(report['evidence_pending'])
        self.assertFalse(report['valid'])
        self.assertEqual(next(c for c in report['checks'] if c['key']=='comparison_conditions')['status'],'fail')

    def test_article_source_change_invalidates_source_scope_request(self):
        from pipeline.focused_quality import audit_article
        seen=[]
        def send(job,step,request):
            if step=='tiered_article_evidence_scope':seen.append(request['messages'][0]['content'])
            fields=request['output_config']['format']['schema']['properties']['checks']['items']['properties']
            checks=[{'key':k,'status':'pass','reason':'fixture','affected_blocks':[],
                     **({'requires_source_check':False,'research_ids':[],'source_urls':[]} if 'requires_source_check' in fields else {})} for k in fields['key']['enum']]
            return {'checks':checks},NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send):
            for pages in (self.pages,[{**self.pages[0],'text':'女性標準プラン有料'},*self.pages[1:]]):
                result=audit_article(None,text=self.text,facts='',outline='',contract={},requirements=self.req,sources=json.dumps(pages))
                self.assertEqual(result['phases']['evidence']['model'],'gpt-6-luna')
        self.assertNotEqual(seen[0],seen[1])

    def test_expanded_source_keeps_other_required_documents(self):
        payload=copy.deepcopy(self.payload)
        payload['candidate_checks'][0]['source_urls']=[self.pages[1]['url']]
        seen=[]
        def send(job,step,request):
            data=json.loads(request['messages'][0]['content']);seen.append(data)
            value=self.message(request)
            value['review'].update(needed=not step.endswith('expanded'),block_ids=[self.a],
                                   source_urls=[self.pages[1]['url']])
            return value,NS(input_tokens=0,output_tokens=0)
        def restore(pages,urls,job):
            return [{**p,'text':p['text']+' additional plan conditions'} for p in pages]
        with patch.object(research,'checked_request',side_effect=send),patch.object(evidence,'restore_requested_bodies',side_effect=restore):
            checks,_,trace=evidence.evidence_audit('research',payload,ROLES['evidence'],self.text)
        self.assertEqual(len(seen),3)
        self.assertEqual([p['url'] for p in seen[-1]['source_documents']],[p['url'] for p in self.pages[:2]])
        self.assertEqual(seen[-1]['source_documents'][0],self.pages[0])
        self.assertIn('additional plan conditions',seen[-1]['source_documents'][1]['text'])
        self.assertFalse(trace[-1]['review']['needed'])

    def test_unchanged_requested_source_does_not_buy_another_review(self):
        payload=copy.deepcopy(self.payload)
        payload['candidate_checks'][0]['source_urls']=[self.pages[1]['url']]
        def send(job,step,request):
            value=self.message(request)
            value['review'].update(needed=True,block_ids=[self.a],source_urls=[self.pages[1]['url']])
            return value,NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send) as calls, \
             patch.object(evidence,'restore_requested_bodies',side_effect=lambda p,u,j:p):
            checks,_,trace=evidence.evidence_audit('research',payload,ROLES['evidence'],self.text)
        self.assertEqual(calls.call_count,2)
        self.assertTrue(trace[-1]['review']['needed'])
        self.assertEqual(checks[0]['status'],'fail')

    def test_article_uses_unspent_outline_allowance_without_raising_quality_total(self):
        with budget.ledger() as ledger:
            ledger['calls']=[{'reserved_usd':.15,'cost_usd':.15,'category':'quality',
                'review_operation':{'phase':'outline','role':'audit','request_sha256':'old'}}]
        request={'messages':[{'content':'{}'}]}
        with budget.request_scope('offline-job','tiered_article_language','article',request):
            with budget.ledger() as ledger:
                self.assertAlmostEqual(budget.review_remaining(ledger),1.1)
                budget.guard_review(ledger,1.0)  # > previous .75 article ceiling
                with self.assertRaises(ContentQualityError):budget.guard_review(ledger,1.11)

    def test_generation_allocation_preserves_quality_budget(self):
        with budget.ledger() as ledger:
            ledger.update(total_limit_usd=5,category_limits_usd={'generation':3.75,'quality':1.25})
            ledger['calls']=[{'reserved_usd':3.7,'cost_usd':3.7,'category':'generation'}]
        with patch.dict(os.environ,{'QUALITY_TOTAL_LIMIT_USD':'5'}),budget.scope('offline-job','article'):
            with self.assertRaisesRegex(ContentQualityError,'他工程'):
                budget.reserve_claude({'model':'claude-opus-5-5','max_tokens':6000,'messages':[]})
        with budget.ledger() as ledger:
            self.assertEqual(len(ledger['calls']),1)
            self.assertEqual(budget.category_remaining(ledger,'quality'),1.25)

    def test_quality_total_is_not_bypassed_by_completion_mode(self):
        with budget.ledger() as ledger:
            ledger.update(total_limit_usd=10,completion_evaluation={'stop_after_usd':10,'authorization':'fixture'})
            ledger['calls']=[{'reserved_usd':1.24,'cost_usd':1.24,'category':'quality',
                'review_operation':{'phase':'article','role':'coverage','request_sha256':'old'}}]
        with patch.dict(os.environ,{'QUALITY_COMPLETION_EVAL':'1'}),budget.request_scope('offline-job','tiered_article_language','new',{'messages':[{'content':'{}'}]}):
            with self.assertRaises(ContentQualityError):
                budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':1000,'input':'x'})
        with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),1)

    def test_source_review_has_its_own_scope_and_response_allowlists(self):
        captured=[]
        def send(job,step,request):
            captured.append(request)
            return self.message(request),NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send):
            evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
        request=captured[0]
        from pipeline.evidence_policy import EVIDENCE_POLICY, REVIEW_RESOLUTION_POLICY
        self.assertIn(EVIDENCE_POLICY,request['system'])
        self.assertIn(REVIEW_RESOLUTION_POLICY,request['system'])
        self.assertNotIn('以下の9項目をすべて判定',request['system'])
        self.assertNotIn('outlineに未使用でも明示',request['system'])
        root=request['output_config']['format']['schema']['properties']
        self.assertEqual(set(root['review']['properties']['block_ids']['items']['enum']),{self.a,self.summary})
        self.assertEqual(root['review']['properties']['source_urls']['items']['enum'],[self.pages[0]['url']])
        self.assertEqual(set(root['checks']['items']['properties']['affected_blocks']['items']['properties']['id']['enum']),{self.a,self.summary})

    def test_unrelated_source_request_cannot_trigger_more_calls_or_become_pass(self):
        # Regression: target conditions are confirmed, but the reviewer asks for
        # other services' prices in the same comparison table.
        self.payload['article_blocks'][2]['text']+='\nBは100円。 https://b.example/price'
        def send(job,step,request):
            value=self.message(request,True)
            value['review']={'needed':True,'block_ids':[self.a],
                'source_urls':[self.pages[1]['url']],
                'reason':'Aの条件は確認できたが、同じ表のBの料金原文がない。'}
            return value,NS(input_tokens=0,output_tokens=0)
        with patch.object(research,'checked_request',side_effect=send) as call:
            with self.assertRaisesRegex(ContentQualityError,'追加確認範囲'):
                evidence.evidence_audit('research',self.payload,ROLES['evidence'],self.text)
        self.assertEqual(call.call_count,1)
        self.assertFalse(scope.source_resolutions('research',self.blocks,self.pages,self.req))

    def test_explicit_credit_retry_retains_cost_and_blocks_second_resend(self):
        request={'messages':[{'content':'{}'}]}
        with budget.request_scope('offline-job','tiered_article_consistency','same',request):
            with budget.ledger() as ledger:
                ledger['calls']=[{'status':'unknown_cost_reserved','reserved_usd':.25,'category':'quality',
                    'review_operation':dict(budget.REQUEST.get()),
                    'retry_authorization':{'reason':'credit_balance_exhausted','request_sha256':'same','user_message':'added credits; continue'}}]
                budget.guard_review(ledger,.1)
                self.assertAlmostEqual(budget.review_remaining(ledger),1.0)
                with self.assertRaises(ContentQualityError):budget.guard_review(ledger,1.01)
                ledger['calls'].append({'status':'pending','reserved_usd':.1,'category':'quality','review_operation':dict(budget.REQUEST.get())})
                with self.assertRaisesRegex(ContentQualityError,'再送'):budget.guard_review(ledger,0)

    def test_review_date_survives_midnight_but_changes_with_snapshot(self):
        from pipeline.quality_context import review_reference_date
        from datetime import datetime, timezone
        first=datetime(2026,10,4,23,59,tzinfo=timezone.utc)
        second=datetime(2026,10,5,0,1,tzinfo=timezone.utc)
        with patch('datetime.datetime') as clock:
            clock.now.return_value=first
            self.assertEqual(review_reference_date('article','same-inputs'),'2026-10-04')
            clock.now.return_value=second
            self.assertEqual(review_reference_date('article','same-inputs'),'2026-10-04')
            self.assertEqual(review_reference_date('article'),'2026-10-04')
            self.assertEqual(review_reference_date('article','changed-text-or-evidence'),'2026-10-05')
            self.assertEqual(review_reference_date('research','new-research'),'2026-10-05')

    def test_unchanged_request_is_cached_after_midnight(self):
        from pipeline.quality_context import review_reference_date
        from datetime import datetime, timezone
        with patch('datetime.datetime') as clock, patch.object(research,'create_with_retry',side_effect=self.fake_provider()) as send:
            for day in (4,5):
                clock.now.return_value=datetime(2026,10,day,23,59,tzinfo=timezone.utc)
                request={'model':'gpt-6-luna','max_tokens':100,'system':'unchanged source review',
                    'messages':[{'role':'user','content':json.dumps({'current_date':review_reference_date('article','unchanged-snapshot')})}],
                    'output_config':{'format':{'schema':{'properties':{'checks':{'items':{'properties':{'key':{'enum':['evidence_support']}}}}}}}}}
                research.checked_request('offline-job','tiered_article_evidence_scope',request)
            self.assertEqual(send.call_count,1)

    def test_reconciled_calendar_duplicate_still_consumes_budget(self):
        req={'messages':[{'content':'{}'}]}
        with budget.request_scope('offline-job','tiered_article_language','new-text',req):
            with budget.ledger() as ledger:
                meta=dict(budget.REQUEST.get())
                ledger['calls']=[{'status':'accounted','reserved_usd':.1,'cost_usd':.1,'category':'quality',
                    'review_operation':{**meta,'request_sha256':f'day-{i}'}} for i in (1,2)]
                with self.assertRaisesRegex(ContentQualityError,'通算回数'):budget.guard_review(ledger,.1)
                ledger['calls'][0]['duplicate_review_reconciliation']={'reason':'calendar_rollover_duplicate',
                    'authorization':'continue repair and recheck','retained_call_index':1,'retained_request_sha256':'day-2'}
                budget.guard_review(ledger,.1)
                self.assertAlmostEqual(budget.review_remaining(ledger),1.05)
                ledger['calls'][0]['duplicate_review_reconciliation']['retained_request_sha256']='wrong'
                with self.assertRaisesRegex(ContentQualityError,'通算回数'):budget.guard_review(ledger,.1)
