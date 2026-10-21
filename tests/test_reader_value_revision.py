"""Revision routing and real request handoff; not model semantic acceptance."""
import copy
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import editorial_blueprint as bp, article_design as ad, content_quality as cq
from pipeline import authoring_policy as ap
from pipeline import lead_review as lr
from pipeline.content_edits import content_blocks
from test_editorial_blueprint import fixture, artifact, verdict
from test_reader_value_flow import response
import test_stage5_authoring as stage5


def lead_finding(saved):
    block=next(b for b in content_blocks(saved['content_text'])
               if saved['meta']['editorial_blueprint']['lead_direction'] in b['text'])
    return {'checks':[{'key':'coverage','status':'fail','reason':'対象と読む理由がつながらない',
        'affected_blocks':[{'id':block['id'],'reason':'リード設計で対象と読む理由を具体化する'}]}]}


class ReaderValueRevisionTests(unittest.TestCase):
    def test_reader_hypothesis_and_underlying_wish_survive_writing_projection(self):
        from pipeline.generation_context import publication_design
        need={'id':'i1','reader_situation':'学び直したいが、また途中で止まるのが気になる',
            'background_emotion':'分からないまま人に聞けず、自信をなくしたくないという仮説',
            'desired_state':'小さな疑問を聞きながら、自分でも進められる感覚を得たい',
            'after_reading':'質問対応のある学び方を選び、質問したい点を準備できる',
            'barrier':'何を質問してよいか分からずためらう',
            'support':[{'kind':'hypothesis','reason':'分析上の推定'}]}
        original={'reader_needs':[need],'answer_scope_policy':'internal adoption policy'}
        projected=publication_design(original)
        for field in ('reader_situation','background_emotion','desired_state','barrier','after_reading'):
            self.assertEqual(projected['reader_needs'][0][field],need[field])
        self.assertNotIn('support',projected['reader_needs'][0])
        self.assertNotIn('answer_scope_policy',projected)
        self.assertIn('support',original['reader_needs'][0])

    def test_adding_a_closing_chapter_invalidates_the_old_outline_review(self):
        _,_,_,ctx,value=fixture();saved=artifact(value,ctx);old_context=bp.review_context(saved)
        review={'valid':True,'blueprint_review_context':old_context,**verdict(old_context)}
        revised=copy.deepcopy(value);closing=copy.deepcopy(value['sections'][0])
        closing.update(id='s99',parent_id='',level=2,title='まとめ：自分に合う方法で始める',
            question='最後に何を選び、どう進めるか',cta='')
        if closing['reader_progression']:
            closing['reader_progression']['next_questions']=[]
        revised['sections'].append(closing)
        revised_artifact=artifact(revised,ctx);bp.require_projection(revised_artifact,ctx)
        with self.assertRaises(cq.ContentQualityError):bp.require_review(review,revised_artifact)
        self.assertEqual(len(saved['meta']['editorial_blueprint']['sections']),len(value['sections']))

    def test_failed_lead_cannot_be_hidden_by_all_passed_decisions_or_global_coverage(self):
        original=stage5.decision_result
        def reply(design,text,missing=None):
            value=original(design,text,missing)
            target=lr.article_block_ids(text)[0]
            value['lead_review']['subject_and_reader']={'status':'fail','reason':'冒頭だけでは主題が特定できない',
                'affected_blocks':[{'id':target,'reason':'主題と対象を冒頭で示す'}]}
            return value
        with patch.object(stage5,'decision_result',side_effect=reply):
            report,requests,_=stage5.Stage5AuthoringTests().audit_fixture('tiered','legacy')
        self.assertFalse(report['valid']);self.assertEqual(len(requests),5)
        phase=report['phases']['coverage']
        self.assertTrue(all(row['status']=='pass' for row in phase['decision_reviews'].values()))
        self.assertEqual(phase['checks'][0]['status'],'fail')
        self.assertTrue(any(f.get('origin')=='individual_lead_review' for f in report['editorial_findings']))

    def test_outline_lead_verdict_routes_to_lead_only_repair(self):
        _,_,_,ctx,value=fixture();saved=artifact(value,ctx);review_ctx=bp.review_context(saved);reply=verdict(review_ctx)
        target=review_ctx['lead_block_ids'][0]
        reply['lead_review']['motivation_bridge']={'status':'fail','reason':'迷いから項目予告へ直行している',
            'affected_blocks':[{'id':target,'reason':'背景と比較を読む理由をつなぐ'}]}
        report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'個別の必要回答は設計されている','affected_blocks':[]}]}
        result=bp.apply_review(report,reply,review_ctx)
        self.assertFalse(result['valid']);self.assertEqual(bp.repair_targets(saved,result),[])
        self.assertTrue(bp.lead_repair_allowed(saved,result))

    def test_missing_or_outside_lead_review_locations_are_rejected(self):
        text='# 記事\n\n主題と読む目的。\n\n## 詳細\n\n本文。';ids=lr.article_block_ids(text)
        value={'lead_review':{key:{'status':'pass','reason':'合成確認','affected_blocks':[]} for key in lr.KEYS}}
        broken=copy.deepcopy(value);broken['lead_review'].pop('reading_payoff')
        with self.assertRaises(cq.ContentQualityError):lr.checked(broken,ids)
        broken=copy.deepcopy(value);broken['lead_review']['subject_and_reader'].update(status='fail',affected_blocks=[{'id':content_blocks(text)[-1]['id'],'reason':'リード以外'}])
        with self.assertRaises(cq.ContentQualityError):lr.checked(broken,ids)
        broken=copy.deepcopy(value);broken['lead_review']['subject_and_reader']['status']='not_applicable'
        with self.assertRaises(cq.ContentQualityError):lr.checked(broken,ids)

    def test_direct_howto_need_not_add_emotional_bridge(self):
        text='# メールが届かない\n\nメールアドレスを確認して一度再送します。';ids=lr.article_block_ids(text)
        value={'lead_review':{key:{'status':'pass','reason':'主題と行動が分かる','affected_blocks':[]} for key in lr.KEYS}}
        value['lead_review']['motivation_bridge'].update(status='not_applicable',reason='心理的背景を使わず、直接手順へつながる')
        self.assertEqual(lr.checked(value,ids),value['lead_review'])

    def test_lead_findings_do_not_authorize_all_chapter_edits(self):
        _,_,_,ctx,value=fixture();saved=artifact(value,ctx);report=lead_finding(saved)
        self.assertTrue(bp.lead_repair_allowed(saved,report))
        self.assertEqual(bp.repair_targets(saved,report),[])
        schema=bp.repair_config(allow_lead=True)['format']['schema']
        self.assertIn('lead_direction',schema['required'])
        self.assertNotIn('titles',schema['properties'])

    def test_lead_only_repair_preserves_chapters_and_invalidates_previous_review(self):
        plan,_,matrix,ctx,value=fixture();saved=artifact(value,ctx);report=lead_finding(saved)
        changed='候補の閲覧と連絡を検討する読者を示し、用途によって必要プランが異なることから、この記事で利用条件を判断できるとつなぐ'
        patch_value={'edits':[],'delete_ids':[],'section_order':[],'lead_direction':changed,'reader_focus':copy.deepcopy(value['reader_focus'])}
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.db.get_job',return_value={}))
            stack.enter_context(patch('pipeline.db.get_artifact',return_value={'content_text':'{"required_sections":[]}'}))
            save=stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=lambda **kw:kw))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled',return_value=False))
            send=stack.enter_context(patch('pipeline.ai.create_with_retry',return_value=response(patch_value)))
            result=bp.repair('job','利用条件',report,saved,None)
        payload=json.loads(send.call_args.kwargs['messages'][0]['content'])
        self.assertTrue(payload['lead_edit_allowed']);self.assertEqual(payload['target_section_ids'],[])
        self.assertEqual(result['meta']['editorial_blueprint']['sections'],value['sections'])
        self.assertEqual(result['meta']['editorial_blueprint']['lead_direction'],changed)
        self.assertFalse(result['meta']['audited'])
        self.assertEqual(json.loads(save.call_args_list[-2].kwargs['content_text'])['valid'],False)
        old=verdict(bp.review_context(saved));old['blueprint_review_context']=bp.review_context(saved)
        with self.assertRaises(cq.ContentQualityError):bp.require_review(old,result)

    def test_unreviewed_or_empty_lead_cannot_be_changed(self):
        _,_,_,ctx,value=fixture()
        patch_value={'edits':[],'delete_ids':[],'section_order':[],'lead_direction':'別の導入','reader_focus':copy.deepcopy(value['reader_focus'])}
        with self.assertRaises(cq.ContentQualityError):
            bp.apply_edits(value,patch_value,ctx,structural=True,targets=['s01'])
        for lead in ('',None,value['lead_direction']):
            with self.subTest(lead=lead),self.assertRaises(cq.ContentQualityError):
                bp.apply_edits(value,{**patch_value,'lead_direction':lead},ctx,structural=False,targets=[],allow_lead=True)

    def test_parent_scope_repair_can_split_and_reparent_its_children_only(self):
        _,_,_,ctx,value=fixture();parent=value['sections'][0]
        parent.update(title='サービスの選び方',question='どの条件で選ぶか')
        child=copy.deepcopy(parent);child.update(id='s02',parent_id='s01',level=3,title='商品Aの紹介',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        grandchild=copy.deepcopy(child);grandchild.update(id='s03',parent_id='s02',level=4,title='料金と条件')
        unrelated=copy.deepcopy(parent);unrelated.update(id='s04',title='利用の手順')
        value['sections']=[parent,child,grandchild,unrelated];saved=artifact(value,ctx)
        block=next(b for b in content_blocks(saved['content_text']) if 'H2：サービスの選び方' in b['text'])
        report={'checks':[{'key':'coverage','status':'fail','requires_source_check':False,
                          'affected_blocks':[{'id':block['id'],'reason':'基準と商品推薦を分ける'}]}]}
        targets=bp.repair_targets(saved,report)
        self.assertEqual(targets,['s01','s02','s03']);self.assertFalse(bp.lead_repair_allowed(saved,report))
        recommendation=copy.deepcopy(parent);recommendation.update(id='s05',title='条件に合うおすすめサービス',question='どの商品を勧めるか')
        moved=copy.deepcopy(child);moved['parent_id']='s05'
        result=bp.apply_edits(value,{'edits':[{'id':'s05','section':recommendation},{'id':'s02','section':moved}],
            'delete_ids':[],'section_order':['s01','s05','s02','s03','s04']},ctx,structural=True,targets=targets)
        self.assertEqual(result['sections'][-1],unrelated)
        self.assertEqual(result['sections'][2]['parent_id'],'s05')
        self.assertEqual(result['lead_direction'],value['lead_direction'])
        self.assertEqual(result['sections'][2]['answer_elements'],child['answer_elements'])

    def test_outline_global_value_failure_survives_passed_individual_decisions(self):
        plan,_,_,ctx,value=fixture();saved=artifact(value,ctx);review_ctx=bp.review_context(saved)
        finding=lead_finding(saved)['checks'][0]
        checks=[{'key':key,'status':'not_applicable' if key in ('prose_quality','redundancy') else 'pass',
                 'reason':'fixture','affected_blocks':[]} for key in cq.CHECKS]
        checks[next(i for i,c in enumerate(checks) if c['key']=='coverage')]=finding
        reply={**verdict(review_ctx),'checks':checks}
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','QUALITY_RESEARCH_ROUTING':'legacy'}),patch.object(cq,'create_with_retry',return_value=response(reply)) as send:
            report=cq.audit(None,stage='research',text=saved['content_text'],outline=saved['content_text'],facts='',
                contract={'required_sections':[]},requirements={'editorial_design':review_ctx,'research_plan':plan,'article_purpose':'cv'},sources='')
        self.assertFalse(report['valid']);self.assertEqual(report['blueprint_issues'],[])
        request=send.call_args.kwargs
        self.assertIn(ap.EDITORIAL_VALUE_REVIEW,request['system'])
        payload=json.loads(request['messages'][0]['content'])
        self.assertEqual(payload['requirements']['editorial_design']['lead_direction'],value['lead_direction'])
        self.assertEqual(payload['requirements']['article_purpose'],'cv')
        self.assertEqual(send.call_count,1)

    def test_existing_five_role_review_receives_lead_and_common_value_rubric(self):
        report,requests,context=stage5.Stage5AuthoringTests().audit_fixture('tiered','legacy')
        self.assertTrue(report['valid']);self.assertEqual(len(requests),5)
        coverage=next(r for r in requests if 'decision_reviews' in r['output_config']['format']['schema']['properties'])
        self.assertIn(ap.EDITORIAL_VALUE_REVIEW,coverage['system'])
        design=json.loads(coverage['messages'][0]['content'])['requirements']['article_design']
        self.assertTrue(design['lead_direction'])
        self.assertEqual(design,context['requirements']['article_design'])
        self.assertEqual(len(report['checks']),9)
