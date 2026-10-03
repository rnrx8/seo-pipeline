import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import content_quality as cq, focused_quality as fq, tiered_evidence as te
from pipeline.quality_context import review_requirements, repair_context
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
        self.assertEqual(calls[0][2]['source_documents'],self.pages)
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
        self.assertEqual(seen[0]['source_documents'],self.pages)
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
                    if c['key'] in ('coverage','evidence_support'):c['status']='fail'
            return result,usage
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            report=cq.audit(None,stage='research',text=self.text,facts='',outline=self.text,contract={},requirements=self.requirements,sources=self.sources)
        self.assertEqual(calls,['tiered_audit_research','tiered_research_evidence_screen'])
        self.assertFalse(report['valid'])
        checks={c['key']:c['status'] for c in report['checks']}
        self.assertEqual(checks['evidence_support'],'pass')
        self.assertEqual(checks['coverage'],'fail')

    def run_evidence(self,send):
        with patch('pipeline.tiered_research.checked_request',side_effect=send):
            return te.evidence_audit('article',{'article_blocks':self.blocks,'source_documents':self.sources},'',fq.ROLES['evidence'],self.text)

    def test_failure_is_reviewed_with_neighbors_and_source_expansion_is_bounded(self):
        calls=[]
        def send(job,step,request):
            data=json.loads(request['messages'][0]['content']);calls.append((step,data))
            if step.endswith('screen'):return self.reply(request,fail=True,review=False)
            return self.reply(request,review=True)
        checks,usage,trace=self.run_evidence(send)
        self.assertEqual(len(calls),3)
        self.assertEqual([p['url'] for p in calls[1][1]['source_documents']],[self.pages[0]['url']])
        self.assertEqual(calls[2][1]['source_documents'],self.pages)
        shown={b['id'] for b in calls[1][1]['article_blocks']}
        self.assertTrue({self.blocks[n]['id'] for n in (1,2,3)}<=shown)
        self.assertTrue(all(c['status']=='fail' for c in checks))
        self.assertEqual(usage.input_tokens,30)

    def test_optional_sol_review_does_not_overrule_unresolved_fact_to_pass(self):
        def send(job,step,request):return self.reply(request,review=True)
        checks,_,_=self.run_evidence(send)
        self.assertTrue(all(c['status']=='fail' for c in checks))

    def test_out_of_scope_and_unknown_source_references_fail_closed(self):
        for mode in ('source','block','missing_review'):
            def send(job,step,request):
                value,usage=self.reply(request,review=True)
                if mode=='source':value['review']['source_urls']=['https://invented.example/']
                elif mode=='block':value['review']['block_ids']=['block-fake']
                else:del value['review']
                return value,usage
            with self.assertRaises(cq.ContentQualityError):self.run_evidence(send)

    def test_passing_sample_still_receives_independent_review(self):
        calls=[]
        def send(job,step,request):calls.append(step);return self.reply(request)
        with patch.object(cq,'digest',return_value='0'*64):self.run_evidence(send)
        self.assertEqual(len(calls),2)

    def test_comparison_claim_cannot_bypass_sol_with_a_luna_pass(self):
        self.text+='\n\nAは最安です。';self.blocks=content_blocks(self.text)
        calls=[]
        def send(job,step,request):calls.append(step);return self.reply(request)
        self.run_evidence(send)
        self.assertEqual(len(calls),2)

    def test_style_repair_omits_raw_sources_factual_repair_keeps_them(self):
        req,source=repair_context(self.requirements,self.sources,[{'key':'prose_quality'}],[])
        self.assertEqual(source,'')
        self.assertEqual(req['custom_prompt'],self.requirements['custom_prompt'])
        self.assertNotIn('research_decisions',req)
        for checks,issues in (([{'key':'comparison_conditions'}],[]),([], [{'key':'missing_required_section'}]),([],[])):
            req,source=repair_context(self.requirements,self.sources,checks,issues)
            self.assertEqual(source,self.sources)
            self.assertNotIn('evidence',req['research_decisions']['items'][0])

    def test_snapshot_invalidates_when_routing_or_sources_change(self):
        args=(self.text,'','',{},self.requirements,self.sources)
        before=cq.snapshot(*args)
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}):self.assertNotEqual(before,cq.snapshot(*args))
        self.assertNotEqual(before,cq.snapshot(*args[:-1],self.sources+' '))

    def test_factual_repair_reuses_only_independently_reviewed_matching_sources(self):
        receipt={'model':'gpt-6.1-sol','review':{'needed':False},'reviewed_block_ids':[self.blocks[2]['id']],
                 'visible_source_urls':[self.pages[0]['url']],
                 'visible_sources_sha256':cq.digest(json.dumps(self.pages[:1],ensure_ascii=False,sort_keys=True))}
        checks=[{'key':'comparison_conditions','affected_blocks':[{'id':self.blocks[2]['id'],'reason':'期間'}]}]
        report={'phases':{'evidence':{'evidence_routing':[receipt]}}}
        _,source=repair_context(self.requirements,self.sources,checks,[],report)
        self.assertEqual(json.loads(source),self.pages[:1])
        for change in ('revision','block','unresolved','no_locations'):
            r=copy.deepcopy(report);c=copy.deepcopy(checks)
            if change=='revision':r['phases']['evidence']['evidence_routing'][0]['visible_sources_sha256']='stale'
            elif change=='block':c[0]['affected_blocks'][0]['id']=self.blocks[4]['id']
            elif change=='unresolved':r['phases']['evidence']['evidence_routing'][0]['review']['needed']=True
            else:c[0]['affected_blocks']=[]
            self.assertEqual(repair_context(self.requirements,self.sources,c,[],r)[1],self.sources)

    def test_new_evidence_route_stops_on_the_existing_budget_before_any_http(self):
        from pipeline import quality_budget as budget
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder,
                'OPENAI_API_KEY':'offline-test','QUALITY_COMPLETION_EVAL':''}),budget.scope('same-job'):
            with budget.ledger() as ledger:
                ledger['calls'].append({'cost_usd':budget.LIMIT,'reserved_usd':budget.LIMIT,'category':'quality'})
            with patch('pipeline.tiered_research.get_optional_artifact',return_value=None),patch('pipeline.openai_review.requests.post') as post:
                with self.assertRaises(cq.ContentQualityError):
                    te.evidence_audit('article',{'article_blocks':self.blocks,'source_documents':self.sources},'',fq.ROLES['evidence'],self.text)
                post.assert_not_called()
            with budget.ledger() as ledger:self.assertEqual(len(ledger['calls']),1)
