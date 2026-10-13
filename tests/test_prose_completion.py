import copy
import json
import os
import socket
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
from types import SimpleNamespace as NS

from pipeline import prose_completion as finish, quality_budget as budget
from pipeline import content_quality as quality, step_content_audit as stage
from pipeline.content_edits import content_blocks
from quality_fixtures import phases


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ARTICLE_LOCAL_PROSE_FINISH='1', ARTICLE_WRITING_FLOW='sonnet_opus', ARTICLE_REVIEW_PROVIDER='tiered')
        self.env.start(); self.addCleanup(self.env.stop)
        self.no_network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)
        self.text = '# 商品比較\n\n## 選び方\n\nどちらの商品も、持ち運びの基本的な機能は、そろっています。\n\n自分に合うものを選べます。'
        self.new = 'どちらの商品も、持ち運びの基本的な機能がそろっています。'
        self.block = content_blocks(self.text)[2]['id']
        self.ctx = dict(facts='確認済みの事実', outline='## 選び方', contract={}, requirements={'custom_prompt':'読者に寄り添う'}, sources='資料')
        self.base = self.report(self.text)

    def report(self, text):
        fingerprint = quality.snapshot(text, **self.ctx)
        loc = {'id': self.block, 'reason': '主述が不自然'}
        values = phases(fingerprint)
        values['language']['checks'][0].update(status='fail', affected_blocks=[loc])
        return dict(stage='article', snapshot=fingerprint, attempt=2, policy_version=quality.POLICY_VERSION,
            valid=False, phases=values, structural_issues=[], readability_issues=[], evidence_pending=False,
            checks=[dict(key=k,status='fail' if k=='prose_quality' else 'pass',reason='確認',
                         affected_blocks=[loc] if k=='prose_quality' else []) for k in quality.CHECKS])

    def verdict(self, bad=None):
        return {'checks':[{'id':self.block,'key':k,'status':'fail' if k==bad else 'pass',
                           'reason':'原文と修正後の対応を確認'} for k in finish.VERDICTS]}

    def execute(self, verdict=None, new=None, checker=None):
        calls=[]
        def send(job, step, request, **kwargs):
            calls.append((step,request))
            if step.endswith('_repair'):return {'edits':[{'id':self.block,'new':new or self.new}]},None
            return verdict or self.verdict(),None
        with patch('pipeline.tiered_research.checked_request', side_effect=send):
            result=finish.complete('job',None,text=self.text,report=self.base,context=self.ctx,
                                   check_candidate=checker or (lambda _:None))
        return result,calls

    def test_success_keeps_original_failed_phases_and_binds_both_snapshots(self):
        (candidate, result), calls = self.execute()
        self.assertEqual(len(calls),2)
        self.assertEqual(result['phases'], self.base['phases'])
        self.assertEqual(result['phases']['language']['checks'][0]['status'],'fail')
        self.assertNotEqual(result['snapshot'],self.base['snapshot'])
        quality.require_audit(result,quality.snapshot(candidate,**self.ctx),stage='article',context={'text':candidate,**self.ctx})
        self.assertLess(len(calls[0][1]['system']),1500)
        self.assertNotIn('確認済みの事実',calls[0][1]['messages'][0]['content'])

    def test_meaning_change_is_rejected_even_when_numbers_are_unchanged(self):
        for new in ('どちらの商品も、持ち運びに最適です。','この商品が、ほかの商品より持ち運びやすくなります。',
                    '専門家の意見ではなく公式が持ち運びに適していると発表しています。'):
            with self.subTest(new=new), self.assertRaises(quality.ContentQualityError):
                self.execute(self.verdict('meaning_preserved'),new)

    def test_every_local_verdict_is_mandatory(self):
        for key in finish.VERDICTS:
            with self.subTest(key=key), self.assertRaises(quality.ContentQualityError):self.execute(self.verdict(key))
        for mutation in ('missing','duplicate','unknown_id'):
            value=self.verdict()
            if mutation=='missing':value['checks'].pop()
            if mutation=='duplicate':value['checks'][-1]=value['checks'][0]
            if mutation=='unknown_id':value['checks'][0]['id']='block-9999'
            with self.subTest(mutation=mutation),self.assertRaises(quality.ContentQualityError):self.execute(value)

    def test_number_negation_quote_and_url_changes_rejected_before_review(self):
        for old,new in [('月額100円です。','月額200円です。'),('無料ではないです。','無料です。'),
                        ('女性のみ無料です。','女性は無料です。'),('「A社」の説明です。','「B社」の説明です。'),
                        ('[申込](https://a.example)です。','[申込](https://b.example)です。')]:
            text='# 比較\n\n## 条件\n\n'+old
            packet={'target_block_ids':['block-0004']}
            with self.subTest(old=old),self.assertRaises(quality.ContentQualityError):
                finish.validate_patch(text,json.dumps({'edits':[{'id':'block-0004','new':new}]}),packet)

    def test_out_of_scope_deletion_and_structure_changes_rejected(self):
        packet=finish.prepare(self.text,self.base,self.ctx['requirements'])
        for edits in ([{'id':self.block,'new':''}],[{'id':self.block,'new':'### 別の見出し'}],
                      [{'id':self.block,'new':'段落。\n\n追加。'}],[{'id':'block-0006','new':'関係ない修正。'}]):
            with self.subTest(edits=edits),self.assertRaises(quality.ContentQualityError):
                finish.validate_patch(self.text,json.dumps({'edits':edits}),packet)

    def test_ineligible_base_never_calls_model(self):
        variants=[]
        for key,value in [('attempt',1),('evidence_pending',True),('structural_issues',[{'key':'missing_table'}]),
                          ('snapshot','stale'),('local_completion',{'x':1})]:
            v=copy.deepcopy(self.base);v[key]=value;variants.append(v)
        v=copy.deepcopy(self.base);v['checks'][0]['status']='fail';variants.append(v)
        v=copy.deepcopy(self.base);v['phases'].pop('evidence');variants.append(v)
        for v in variants:
            with patch('pipeline.tiered_research.checked_request') as api:
                self.assertIsNone(finish.complete('job',None,text=self.text,report=v,context=self.ctx,check_candidate=lambda _:None))
                api.assert_not_called()

    def test_heading_and_table_findings_are_not_prose_finish_targets(self):
        for target in ('# 記事','|項目|値|\n|---|---|\n|A|B|','- 選択肢','![図](https://a.example)'):
            self.text='# 比較\n\n## 条件\n\n'+target;self.base=self.report(self.text)
            self.assertFalse(finish.eligible(self.base,self.text,quality.snapshot(self.text,**self.ctx)))

    def test_mechanical_failure_prevents_second_paid_call(self):
        checked=[]
        def reject(candidate):
            checked.append(candidate)
            raise quality.ContentQualityError('structure')
        with patch('pipeline.tiered_research.checked_request',return_value=({'edits':[{'id':self.block,'new':self.new}]},None)) as api:
            with self.assertRaises(quality.ContentQualityError):
                finish.complete('job',None,text=self.text,report=self.base,context=self.ctx,check_candidate=reject)
            self.assertEqual(api.call_count,1)
            self.assertEqual(len(checked),1)

    def test_stale_or_tampered_completion_cannot_be_accepted(self):
        (candidate,result),_=self.execute()
        for field in ('text','facts','outline','contract','requirements','sources'):
            ctx={'text':candidate,**self.ctx};ctx[field]=dict(extra='changed') if field in ('contract','requirements') else ctx[field]+'変更'
            with self.subTest(field=field),self.assertRaises(quality.ContentQualityError):
                quality.require_audit(result,quality.snapshot(**ctx),stage='article',context=ctx)
        for mutation in ('review','base','phases','binding'):
            r=copy.deepcopy(result)
            if mutation=='review':r['local_completion']['review']['checks'].pop()
            if mutation=='base':r['local_completion']['base_text']+='変更'
            if mutation=='phases':r['phases']['language']['checks'][0]['status']='pass'
            if mutation=='binding':r['local_completion']['review_input_sha256']='wrong'
            with self.subTest(mutation=mutation),self.assertRaises(quality.ContentQualityError):
                quality.require_audit(r,quality.snapshot(candidate,**self.ctx),stage='article',context={'text':candidate,**self.ctx})
        with self.assertRaises(quality.ContentQualityError):quality.require_audit(result,result['snapshot'])
        with self.assertRaises(quality.ContentQualityError):quality.require_audit(result,result['snapshot'],stage='article')

    def test_feature_is_off_by_default_and_cannot_run_in_legacy_mode(self):
        with patch.dict(os.environ,ARTICLE_LOCAL_PROSE_FINISH='0'),patch('pipeline.tiered_research.checked_request') as api:
            self.assertFalse(finish.enabled())
            self.assertIsNone(finish.complete('job',None,text=self.text,report=self.base,context=self.ctx,check_candidate=lambda _:None))
            api.assert_not_called()
        with patch.dict(os.environ,ARTICLE_WRITING_FLOW='legacy'):
            with self.assertRaises(quality.ContentQualityError):finish.enabled()

    def test_local_policy_change_invalidates_receipt_not_full_audit(self):
        (candidate,result),_=self.execute()
        original=quality.snapshot(self.text,**self.ctx)
        with patch.dict(os.environ,ARTICLE_LOCAL_PROSE_FINISH='0'):
            self.assertEqual(original,quality.snapshot(self.text,**self.ctx))
        with patch.object(finish,'REVIEW_SYSTEM',finish.REVIEW_SYSTEM+'変更'):
            self.assertEqual(original,quality.snapshot(self.text,**self.ctx))
            with self.assertRaises(quality.ContentQualityError):
                quality.require_audit(result,result['snapshot'],stage='article',context={'text':candidate,**self.ctx})

    def test_stage_publishes_only_after_local_review_and_keeps_failed_audit(self):
        for passed in (False, True):
            saved={}
            with self.subTest(passed=passed),ExitStack() as stack:
                stack.enter_context(patch.object(stage,'get_job',return_value={}))
                def artifact(job,step):
                    value={'article':self.text,'fact_sheet':self.ctx['facts'],
                           'outline':self.ctx['outline'],'content_contract':'{}',
                           'content_audit':json.dumps(self.base)}.get(step,'')
                    return {'content_text':value,'meta':{'section_map':[]}}
                stack.enter_context(patch.object(stage,'get_artifact',side_effect=artifact))
                stack.enter_context(patch.object(stage,'audit_facts',return_value=self.ctx['facts']))
                stack.enter_context(patch.object(stage,'source_evidence',return_value=self.ctx['sources']))
                stack.enter_context(patch.object(stage,'final_review_requirements',return_value=self.ctx['requirements']))
                stack.enter_context(patch.object(stage.anthropic,'Anthropic'))
                stack.enter_context(patch.object(stage,'bind_sections',return_value=[]))
                stack.enter_context(patch.object(stage,'carry_sections',return_value=[]))
                validators=[stack.enter_context(patch.object(stage,name,return_value=[])) for name in
                            ('validate_delivery','readability_issues','validate_structure','cta_placement_issues','scope_issues','explicit_risk_guarantees')]
                stack.enter_context(patch.object(stage,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:kw}) or kw))
                audit=stack.enter_context(patch.object(stage,'audit',side_effect=AssertionError('must resume checkpoint')))
                api=stack.enter_context(patch('pipeline.tiered_research.checked_request',side_effect=[
                    ({'edits':[{'id':self.block,'new':self.new}]},None),
                    (self.verdict(None if passed else 'meaning_preserved'),None)]))
                if passed:
                    stage.run('job','比較')
                    self.assertIn(self.new,saved['article']['content_text'])
                    self.assertTrue(saved['article']['meta']['content_audited'])
                    self.assertIn('content_audit_local_completion',saved)
                else:
                    with self.assertRaises(quality.ContentQualityError):stage.run('job','比較')
                    self.assertNotIn('article',saved)
                    self.assertFalse(json.loads(saved['content_audit']['content_text'])['valid'])
                self.assertFalse(json.loads(saved['content_audit_2']['content_text'])['valid'])
                self.assertEqual(api.call_count,2)
                audit.assert_not_called()
                for validator in validators:validator.assert_called()

    def test_failed_review_is_cached_on_restart_without_more_spending(self):
        from pipeline import tiered_research as research
        saved={}
        with ExitStack() as stack:
            directory=stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.dict(os.environ,QUALITY_BUDGET_DIR=directory,QUALITY_TOTAL_LIMIT_USD='1',QUALITY_COMPLETION_EVAL=''))
            stack.enter_context(budget.scope('job'))
            stack.enter_context(patch.object(research,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))))
            stack.enter_context(patch.object(research,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:copy.deepcopy(kw)})))
            def provider(client,**request):
                if request['model'].startswith('claude'):
                    reservation=budget.reserve_claude({'model':request['model'],'max_tokens':100,'messages':[]})
                else:
                    reservation=budget.reserve({'model':request['model'],'max_output_tokens':100,'input':'offline fixture'})
                budget.settle(reservation,{'input_tokens':100,'output_tokens':20})
                value=({'edits':[{'id':self.block,'new':self.new}]} if request['system']==finish.REPAIR_SYSTEM
                       else self.verdict('natural_language'))
                return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(value))],usage=NS(input_tokens=100,output_tokens=20))
            api=stack.enter_context(patch.object(research,'create_with_retry',side_effect=provider))
            for _ in range(2):
                with self.assertRaisesRegex(quality.ContentQualityError,'意味保持・解消・自然さ・接続'):
                    finish.complete('job',None,text=self.text,report=self.base,context=self.ctx,check_candidate=lambda _:None)
            self.assertEqual(api.call_count,2)
            with budget.ledger() as value:self.assertEqual(len(value['calls']),2)


class CompletionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,QUALITY_BUDGET_DIR=self.tmp.name,QUALITY_TOTAL_LIMIT_USD='0.3',QUALITY_COMPLETION_EVAL='')
        env.start();self.addCleanup(env.stop)

    def charge(self,step,fingerprint,amount=.02):
        with budget.request_scope('job',step,fingerprint,{'messages':[{'content':'{}'}]}):
            with budget.ledger() as value:
                value.setdefault('total_limit_usd',.3)
                budget.guard_total(value,amount);budget.guard_review(value,amount)
                value['calls'].append({'status':'accounted','category':'quality','cost_usd':amount,'reserved_usd':amount,
                                      'review_operation':dict(budget.REQUEST.get())})

    def test_restart_cannot_add_second_finish_or_extend_its_limit(self):
        self.charge('tiered_content_repair_1_0','first')
        self.charge('tiered_prose_finish_repair','finish')
        self.charge('tiered_prose_finish_review','review')
        with budget.scope('job'),budget.ledger() as value:
            value['additional_review_authorization']={'phase':'article','extra_calls_per_role':3,'user_message':'test'}
        for step in ('tiered_prose_finish_repair','tiered_prose_finish_review'):
            with self.subTest(step=step),self.assertRaises(quality.ContentQualityError):self.charge(step,'different')

    def test_encoding_retry_already_consumed_repair_allowance(self):
        self.charge('tiered_content_repair_1_0','first')
        self.charge('tiered_content_repair_1_1','encoding')
        with self.assertRaises(quality.ContentQualityError):self.charge('tiered_prose_finish_repair','finish')

    def test_no_new_budget_for_finish(self):
        self.charge('tiered_content_repair_1_0','first',.29)
        with self.assertRaises(quality.ContentQualityError):self.charge('tiered_prose_finish_repair','finish',.02)

    def test_unsettled_finish_cannot_be_resent_after_restart(self):
        self.charge('tiered_prose_finish_repair','interrupted')
        with budget.scope('job'),budget.ledger() as value:
            value['calls'][-1]['status']='unknown_cost_reserved'
        with self.assertRaises(quality.ContentQualityError):
            self.charge('tiered_prose_finish_repair','interrupted')
