import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import content_quality as cq, focused_quality as fq, tiered_evidence as te
from pipeline.quality_context import review_requirements
from pipeline.review_scope import repair_packet
from pipeline.content_edits import content_blocks


class CompactQualityFlowTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'})
        env.start();self.addCleanup(env.stop)
        self.text='# 比較\n\n## 料金\n\n月額の対象は男性です。\n\n年契約です。\n\n## まとめ\n\n期間を確認してください。'
        # A non-sampled fixture; sample path gets its own explicit test.
        while int(cq.digest(json.dumps(content_blocks(self.text),ensure_ascii=False))[:8],16)%10==0:self.text+='。'
        self.blocks=content_blocks(self.text)
        self.pages=[{'url':'https://a.example/price','text':'男性は年契約で月額100円。税込み。','truncated':False},
                    {'url':'https://b.example/price','text':'女性は基本機能無料。対象の条件あり。','truncated':True}]
        self.sources=json.dumps(self.pages,ensure_ascii=False)
        self.requirements={'custom_prompt':'自社を優先。ただし嘘を書かない','learned_style_rules':['自然な日本語'],
            'research_plan':{'items':[{'id':'q1','required':True,'priority':'essential','question':'契約期間は？'}]},
            'research_decisions':{'coverage_sufficient':True,'items':[{'id':'q1','verified':True,'status':'confirmed',
                'answer':'男性・年契約。','applicable_at':'2025年','supports_current_conclusion':False,
                'basis':'historical','reason':'過去時点','new_condition':'将来追加された条件も維持',
                'omission_reason':'','evidence':[{'url':self.pages[0]['url'],'quote':self.pages[0]['text'],
                  'source_kind':'primary','expert_scope_reason':'適用範囲'}]}]}}

    def reply(self,request,fail=False,review=False):
        root=request['output_config']['format']['schema'];fields=root['properties']['checks']['items']['properties']
        result={'checks':[{'key':k,'status':'fail' if fail else 'pass','reason':'fixture',
            **({'affected_blocks':[{'id':self.blocks[2]['id'],'reason':'契約期間'}] if fail else []}
               if 'affected_blocks' in fields else {})} for k in fields['key']['enum']]}
        if 'requires_source_check' in fields:
            for c in result['checks']:
                c.update(requires_source_check=False,source_urls=[self.pages[0]['url']],research_ids=['q1'])
        if 'review' in root['properties']:
            result['review']={'needed':review,'block_ids':[self.blocks[2]['id']] if review else [],
                              'source_urls':[self.pages[0]['url']],'reason':'契約条件を確認'}
        return result,NS(input_tokens=10,output_tokens=2)

    def test_brief_keeps_every_question_answer_limit_and_user_setting_without_quoting_twice(self):
        before=copy.deepcopy(self.requirements)
        for role in ('research','coverage','evidence'):
            result=review_requirements(self.requirements,role)
            self.assertEqual(result['research_plan']['items'],before['research_plan']['items'])
            item=result['research_decisions']['items'][0]
            for key in ('id','answer','status','verified','applicable_at','supports_current_conclusion','new_condition','omission_reason'):
                self.assertEqual(item[key],before['research_decisions']['items'][0][key])
            self.assertNotIn('evidence',item)
            self.assertEqual(item['evidence_sources'][0]['url'],self.pages[0]['url'])
            self.assertEqual(result['custom_prompt'],before['custom_prompt'])
        self.assertEqual(self.requirements,before)

    def test_article_keeps_five_roles_and_all_blocks_with_luna_fact_check(self):
        calls=[]
        def send(job,step,request):
            calls.append((step,request,json.loads(request['messages'][0]['content'])))
            return self.reply(request)
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=fq.audit_article(None,text=self.text,facts='',outline='',contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(set(report['phases']),set(fq.ROLES))
        self.assertEqual(len(calls),5)
        self.assertEqual(calls[0][1]['model'],'gpt-6-luna')
        self.assertNotIn('source_documents',calls[0][2])
        for step,request,data in calls:
            self.assertEqual(data['article_blocks'],self.blocks)
            self.assertEqual(data['requirements']['custom_prompt'],self.requirements['custom_prompt'])
            if step!='tiered_article_evidence_screen':self.assertNotIn('source_documents',data)

    def test_canonical_answers_appear_once_without_removing_original_fact_sources(self):
        facts='CANONICAL_FACT_TEXT'
        self.requirements['research_decisions'].update(valid=True,facts_sha256=cq.digest(facts))
        seen=[]
        def send(job,step,request):
            seen.append(json.loads(request['messages'][0]['content']))
            return self.reply(request)
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            fq.audit_article(None,text=self.text,facts=facts,outline='',contract={},requirements=self.requirements,sources=self.sources)
        for payload in seen:
            if 'confirmed_facts' in payload:
                self.assertNotEqual(payload['confirmed_facts'],facts)
                self.assertEqual(payload['requirements']['research_decisions']['items'][0]['answer'],'男性・年契約。')
        self.assertNotIn('source_documents',seen[0])
        self.assertEqual(len(seen),5)

    def test_outline_preserves_all_checks_but_sol_receives_no_source_bodies(self):
        calls=[]
        def send(job,step,request):
            calls.append((step,request,json.loads(request['messages'][0]['content'])))
            return self.reply(request)
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]['model'],'gpt-6.1-sol')
        self.assertNotIn('source_documents',calls[0][2])
        self.assertIn('source_revision',calls[0][2])
        self.assertEqual({c['key'] for c in report['checks']},set(cq.CHECKS))
        self.assertEqual(report['input_tokens'],10)

    def test_outline_fact_failure_gets_source_check_without_erasing_coverage_failure(self):
        calls=[]
        def send(job,step,request):
            calls.append(step)
            result,usage=self.reply(request)
            if step=='tiered_audit_research':
                for c in result['checks']:
                    if c['key'] in ('coverage','evidence_support'):
                        c.update(status='fail',affected_blocks=[{'id':self.blocks[2]['id'],'reason':'fixture'}],
                                 requires_source_check=c['key']=='evidence_support')
            return result,usage
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(calls,['tiered_audit_research','tiered_research_evidence_screen'])
        self.assertFalse(report['valid'])
        checks={c['key']:c['status'] for c in report['checks']}
        self.assertEqual(checks['evidence_support'],'pass')
        self.assertEqual(checks['coverage'],'fail')

    def run_evidence(self,send):
        with patch('pipeline.tiered_research.checked_request',side_effect=send), \
             patch('pipeline.db.get_optional_artifact',return_value=None):
            return te.evidence_audit('article',{'article_blocks':self.blocks,'source_documents':self.sources,
                'candidate_checks':[{'key':'evidence_support','status':'fail','requires_source_check':True,
                    'reason':'条件の確認','source_urls':[self.pages[0]['url']],'research_ids':[],
                    'affected_blocks':[{'id':self.blocks[2]['id'],'reason':'期間'}]}]},'',fq.ROLES['evidence'],self.text)

    def test_restore_only_requested_saved_source_without_another_fetch(self):
        pages=[{'url':'https://a.example','text':'head\n[中略：取得本文の抜粋]\ntail','truncated':True,'fetched_at':'today'},
               {'url':'https://b.example','text':'other','truncated':True,'fetched_at':'today'}]
        raw=[{**pages[0],'text':'head important billing condition tail','status':'success','truncated':False},
             {**pages[1],'text':'other full body','status':'success','truncated':False}]
        with patch('pipeline.db.get_optional_artifact',return_value={'content_text':json.dumps(raw)}):
            result=te.restore_requested_bodies(pages,['https://a.example/'],'j')
        self.assertEqual(result[0]['text'],raw[0]['text'])
        self.assertFalse(result[0]['truncated'])
        self.assertEqual(result[1],pages[1])
        raw[0]['fetched_at']='different fetch'
        with patch('pipeline.db.get_optional_artifact',return_value={'content_text':json.dumps(raw)}):
            self.assertEqual(te.restore_requested_bodies(pages,['https://a.example'],'j'),pages)

    def test_known_outline_condition_error_goes_to_repair_without_rereading_sources(self):
        calls=[]
        def send(job,step,request):
            calls.append(step)
            result,usage=self.reply(request)
            for c in result['checks']:
                c['requires_source_check']=False
                if c['key']=='evidence_support':
                    c.update(status='fail',reason='採用済みの決済方法別条件が構成から欠落。条件を補う。',affected_blocks=[{'id':self.blocks[2]['id'],'reason':'条件'}])
            return result,usage
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,
                            contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(calls,['tiered_audit_research'])
        self.assertFalse(report['valid'])  # Minor does not mean publish unchanged.

    def test_source_uncertainty_does_not_erase_a_separate_known_condition_error(self):
        calls=[]
        def send(job,step,request):
            calls.append(step)
            result,usage=self.reply(request)
            if step=='tiered_audit_research':
                for c in result['checks']:
                    c['requires_source_check']=False
                    if c['key']=='comparison_conditions':c.update(status='fail',reason='契約期間が欠落',affected_blocks=[{'id':self.blocks[2]['id'],'reason':'期間'}])
                    if c['key']=='evidence_support':c.update(status='fail',requires_source_check=True,reason='機能表との対応確認が必要',affected_blocks=[{'id':self.blocks[2]['id'],'reason':'機能'}])
            return result,usage
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,
                            contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(len(calls),2)
        self.assertEqual(next(c for c in report['checks'] if c['key']=='evidence_support')['status'],'pass')
        self.assertEqual(next(c for c in report['checks'] if c['key']=='comparison_conditions')['status'],'fail')
        self.assertFalse(report['valid'])

    def test_clear_source_typo_stays_failed_without_automatic_sol_escalation(self):
        calls=[]
        def send(job,step,request):
            calls.append(step)
            return self.reply(request,fail=True,review=False)
        checks,_,trace=self.run_evidence(send)
        self.assertEqual(len(calls),1)
        self.assertFalse(trace[-1]['review']['needed'])
        self.assertTrue(all(c['status']=='fail' for c in checks))

    def test_invalid_source_routing_is_rejected_before_another_paid_call(self):
        def send(job,step,request):
            value,usage=self.reply(request)
            value['checks'][0]['requires_source_check']='false'
            return value,usage
        with patch('pipeline.tiered_research.checked_request',side_effect=send) as call:
            with self.assertRaises(cq.ContentQualityError):
                cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,
                         contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(call.call_count,1)

    def test_failure_is_reviewed_with_neighbors_and_source_expansion_is_bounded(self):
        calls=[]
        def send(job,step,request):
            data=json.loads(request['messages'][0]['content']);calls.append((step,data))
            if step.endswith('screen'):return self.reply(request,fail=True,review=True)
            return self.reply(request,review=True)
        checks,usage,trace=self.run_evidence(send)
        self.assertEqual(len(calls),2)
        self.assertEqual([p['url'] for p in calls[1][1]['source_documents']],[self.pages[0]['url']])
        self.assertTrue(all(self.pages[1] not in c[1]['source_documents'] for c in calls))
        shown={b['id'] for b in calls[1][1]['article_blocks']}
        self.assertTrue({self.blocks[n]['id'] for n in (1,2,3)}<=shown)
        self.assertEqual(next(c for c in checks if c['key']=='evidence_support')['status'],'fail')
        self.assertTrue(all(c['status']=='pass' for c in checks if c['key']!='evidence_support'))
        self.assertEqual(usage.input_tokens,20)

    def test_optional_sol_review_does_not_overrule_unresolved_fact_to_pass(self):
        def send(job,step,request):return self.reply(request,review=True)
        checks,_,_=self.run_evidence(send)
        self.assertEqual(next(c for c in checks if c['key']=='evidence_support')['status'],'fail')
        self.assertTrue(all(c['status']=='pass' for c in checks if c['key']!='evidence_support'))

    def test_comparison_paragraph_links_do_not_expand_explicit_source_scope(self):
        self.text=self.text.replace('月額の対象は男性です。', '月額の対象は男性です。 https://b.example/price')
        self.blocks=content_blocks(self.text)
        calls=[]
        def send(job,step,request):
            data=json.loads(request['messages'][0]['content']);calls.append(step)
            if step.endswith('screen'):return self.reply(request,fail=True,review=True)
            self.assertEqual(data['source_documents'],self.pages[:1])
            return self.reply(request)
        checks,_,_=self.run_evidence(send)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(c['status']=='pass' for c in checks))

    def test_out_of_scope_and_unknown_source_references_fail_closed(self):
        for mode in ('source','block','missing_review'):
            def send(job,step,request):
                value,usage=self.reply(request,review=True)
                if mode=='source':value['review']['source_urls']=['https://invented.example/']
                elif mode=='block':value['review']['block_ids']=['block-fake']
                else:del value['review']
                return value,usage
            with self.assertRaises(cq.ContentQualityError):self.run_evidence(send)

    def test_known_source_trailing_slash_does_not_require_another_model_response(self):
        calls=[]
        def send(job,step,request):
            calls.append(step)
            value,usage=self.reply(request,review=step.endswith('screen'))
            value['review']['source_urls']=[self.pages[0]['url']+'/']
            return value,usage
        checks,_,trace=self.run_evidence(send)
        self.assertTrue(all(c['status']=='pass' for c in checks))
        self.assertEqual(len(calls),2)
        self.assertEqual(trace[0]['review']['source_urls'],[self.pages[0]['url']])

    def test_document_hash_does_not_trigger_paid_sampling(self):
        calls=[]
        def send(job,step,request):calls.append(step);return self.reply(request)
        with patch.object(cq,'digest',return_value='0'*64):self.run_evidence(send)
        self.assertEqual(len(calls),1)

    def test_comparison_keyword_alone_does_not_trigger_another_review(self):
        self.text+='\n\nAは最安です。';self.blocks=content_blocks(self.text)
        calls=[]
        def send(job,step,request):calls.append(step);return self.reply(request)
        self.run_evidence(send)
        self.assertEqual(len(calls),1)

    def test_style_repair_omits_sources_but_preserves_user_settings(self):
        report={'checks':[{'key':'prose_quality','status':'fail','affected_blocks':[{'id':self.blocks[2]['id'],'reason':'文章'}]}]}
        packet=repair_packet(self.text,review_requirements(self.requirements,'evidence'),self.sources,report)
        self.assertEqual(packet['source_documents'],[])
        self.assertEqual(packet['requirements']['custom_prompt'],self.requirements['custom_prompt'])
        self.assertEqual(packet['requirements']['research_decisions']['items'],[])

    def test_snapshot_invalidates_when_routing_or_sources_change(self):
        args=(self.text,'','',{},self.requirements,self.sources)
        before=cq.snapshot(*args)
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}):self.assertNotEqual(before,cq.snapshot(*args))
        self.assertNotEqual(before,cq.snapshot(*args[:-1],self.sources+' '))

    def test_factual_repair_uses_explicit_sources_and_rejects_missing_locations(self):
        check={'key':'comparison_conditions','status':'fail','research_ids':['q1'],
               'affected_blocks':[{'id':self.blocks[2]['id'],'reason':'期間'}]}
        packet=repair_packet(self.text,review_requirements(self.requirements,'evidence'),self.sources,{'checks':[check]})
        self.assertEqual(packet['source_documents'],self.pages[:1])
        with self.assertRaises(cq.ContentQualityError):
            repair_packet(self.text,self.requirements,self.sources,{'checks':[{**check,'affected_blocks':[]}]})

    def test_new_evidence_route_stops_on_the_existing_budget_before_any_http(self):
        from pipeline import quality_budget as budget
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder,
                'OPENAI_API_KEY':'offline-test','QUALITY_COMPLETION_EVAL':''}),budget.scope('same-job'):
            with budget.ledger() as ledger:
                ledger['calls'].append({'cost_usd':budget.LIMIT,'reserved_usd':budget.LIMIT,'category':'quality'})
            with patch('pipeline.tiered_research.get_optional_artifact',return_value=None),patch('pipeline.openai_review.requests.post') as post:
                with self.assertRaises(cq.ContentQualityError):
                    te.evidence_audit('article',{'article_blocks':self.blocks,'source_documents':self.sources,'candidate_checks':[{'key':'evidence_support','status':'fail','requires_source_check':True,'reason':'期間','source_urls':[self.pages[0]['url']],'research_ids':[],'affected_blocks':[{'id':self.blocks[2]['id'],'reason':'期間'}]}]},'',fq.ROLES['evidence'],self.text)
                post.assert_not_called()
            with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),1)
