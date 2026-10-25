"""Synthetic controls for section changes, purposeful marketing and their handoff."""
import copy
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import editorial_blueprint as bp,content_quality as cq
from test_editorial_blueprint import fixture,artifact,verdict
from test_reader_value_flow import response


def chain():
    plan,pages,matrix,ctx,value=fixture()
    first=value['sections'][0]
    first['reader_progression']['after'].update(decision='',emotion='必要な機能の違いが見え、検討の見通しを持てることを狙う')
    first['reader_progression']['next_questions']=[{'question':'自分にはどちらの動作が必要か','reason':'条件の違いを理解し、自分の用途へ当てはめる段階になる',
        'handling':'next_section','target_section_id':'s02'}]
    second=copy.deepcopy(first);second.update(id='s02',title='自分の用途に合う選択',question='自分の用途にはどの動作が必要か',conclusion='候補を見たい場合と連絡したい場合を分けて選ぶ')
    second['reader_progression']={'before':{'understanding':'閲覧と送信の適用条件の違いを理解している','open_questions':['自分にはどちらが必要か'],'emotion':''},
        'after':{'resolved_questions':['自分の用途に必要な動作は何か'],'understanding':'','decision':'閲覧だけで検討するか、送信も使うかを選べる','action':'','emotion':'選択の迷いが整理されることを狙う'},
        'next_questions':[{'question':'利用前に何を確かめるか','reason':'使いたい動作が決まり、利用前の確認が次の課題になる','handling':'next_section','target_section_id':'s03'}],
        'marketing':{'role':'潜在的な迷いへの気づきと価値理解','mechanism':'候補を見る段階と連絡する段階を具体例で想像できるようにする','next_action':'自分が使いたい動作を整理する'}}
    third=copy.deepcopy(first);third.update(id='s03',title='利用前に確認すること',question='利用前に何を確認すればよいか',conclusion='選んだ動作と適用プランの対応を確認する')
    third['reader_progression']={'before':{'understanding':'使いたい動作を選んでいる','open_questions':['利用前に何を確認するか'],'emotion':''},
        'after':{'resolved_questions':['利用前の確認項目は何か'],'understanding':'','decision':'','action':'使いたい動作と適用プランの対応を確認する','emotion':''},
        'next_questions':[],'marketing':None}
    value['sections']=[first,second,third]
    return plan,pages,matrix,ctx,value


class ReaderProgressionTests(unittest.TestCase):
    def test_three_chapters_resolve_local_questions_and_connect_to_next(self):
        _,_,_,ctx,value=chain();bp.validate(value,ctx);a=artifact(value,ctx);bp.require_projection(a,ctx)
        text=a['content_text']
        for term in ('この章で解消する疑問','狙う感情の変化','次の問い','記事目的への役割','s02','s03'):
            self.assertIn(term,text)
        self.assertEqual(a['meta']['reader_trace'][0]['next_section_ids'],['s02'])
        self.assertEqual(a['meta']['reader_trace'][2]['next_section_ids'],[])

    def test_every_chapter_need_not_make_a_decision_or_complete_whole_article(self):
        _,_,_,ctx,value=chain()
        self.assertFalse(value['sections'][0]['reader_progression']['after']['decision'])
        self.assertFalse(value['sections'][2]['reader_progression']['after']['understanding'])
        bp.validate(value,ctx)

    def test_emotion_and_marketing_are_optional_even_on_h2(self):
        _,_,_,ctx,value=fixture();bp.validate(value,ctx)
        text=bp.render(value,ctx)
        self.assertNotIn('狙う感情の変化',text);self.assertNotIn('記事目的への役割',text);self.assertNotIn('次の問い',text)

    def test_child_can_inherit_parent_without_copying_state(self):
        _,_,_,ctx,value=fixture();child=copy.deepcopy(value['sections'][0]);child.update(id='s02',level=3,parent_id='s01',title='具体例',reader_progression=None,budget={'chars':0,'importance':0,'reason':''})
        value['sections'].append(child);bp.validate(value,ctx)
        self.assertEqual(bp.render(value,ctx).count('この章で解消する疑問'),1)
        self.assertEqual(bp.review_context(artifact(value,ctx))['progression_section_ids'],['s01'])

    def test_child_can_define_only_its_own_change(self):
        _,_,_,ctx,value=fixture();child=copy.deepcopy(value['sections'][0]);child.update(id='s02',level=3,parent_id='s01',title='用途を具体化',budget={'chars':0,'importance':0,'reason':''})
        child['reader_progression']['before']={'understanding':'','open_questions':[],'emotion':''}
        child['reader_progression']['after'].update(resolved_questions=['自分は候補を見たいだけか'],understanding='自分の用途を具体化できる',decision='')
        value['sections'].append(child);bp.validate(value,ctx)
        self.assertEqual(bp.review_context(artifact(value,ctx))['progression_section_ids'],['s01','s02'])

    def test_h2_cannot_omit_its_local_state_change(self):
        _,_,_,ctx,value=fixture()
        for change in ('null','before','resolved','outcome'):
            v=copy.deepcopy(value);p=v['sections'][0]['reader_progression']
            if change=='null':v['sections'][0]['reader_progression']=None
            elif change=='before':p['before'].update(understanding='',open_questions=[])
            elif change=='resolved':p['after']['resolved_questions']=[]
            else:
                for key in ('understanding','decision','action','emotion'):p['after'][key]=''
            with self.subTest(change=change),self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)

    def test_dangling_backwards_self_and_cyclic_next_links_are_rejected(self):
        _,_,_,ctx,value=chain()
        for target in ('s99','s01'):
            v=copy.deepcopy(value);v['sections'][0]['reader_progression']['next_questions'][0]['target_section_id']=target
            with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)
        v=copy.deepcopy(value);v['sections'][1]['reader_progression']['next_questions'][0]['target_section_id']='s01'
        with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)

    def test_same_section_and_external_scope_are_explicit(self):
        _,_,_,ctx,value=fixture();p=value['sections'][0]['reader_progression']
        for handling,target in (('same_section','s01'),('related',''),('out_of_scope','')):
            p['next_questions']=[{'question':'さらに知りたいこと','reason':'章の理解から生まれる補助的な問い','handling':handling,'target_section_id':target}]
            bp.validate(value,ctx)
            self.assertEqual(bp.trace(value,ctx)[0]['next_section_ids'],[])
        p['next_questions'][0]['target_section_id']='s01'
        with self.assertRaises(cq.ContentQualityError):bp.validate(value,ctx)

    def test_next_question_requires_a_reason_not_just_a_related_topic(self):
        _,_,_,ctx,value=chain();value['sections'][0]['reader_progression']['next_questions'][0]['reason']=''
        with self.assertRaises(cq.ContentQualityError):bp.validate(value,ctx)

    def test_latent_need_expectation_and_guidance_are_allowed(self):
        _,_,_,ctx,value=chain();p=value['sections'][1]['reader_progression']
        p['before']['emotion']='何となく負担を感じているかもしれないという仮説'
        p['after']['emotion']='見落としていた負担に気づき、改善への期待が生まれることを狙う'
        p['marketing'].update(role='潜在課題の顕在化',mechanism='日常の迷いを場面例で引き出し、確認済みの機能が役立つ状況を示す',next_action='用途に合う選択肢を比較する')
        bp.validate(value,ctx)
        text=bp.render(value,ctx);self.assertIn('潜在課題の顕在化',text);self.assertIn('狙う感情',text)
        self.assertEqual(value['sections'][1]['cta'],'')

    def test_awareness_marketing_need_not_force_a_purchase_action(self):
        _,_,_,ctx,value=chain();value['sections'][1]['reader_progression']['marketing']['next_action']=''
        bp.validate(value,ctx)
        text=bp.render(value,ctx);self.assertIn('記事目的への役割',text);self.assertNotIn('導きたい次の行動',text)

    def test_blank_marketing_mechanism_is_not_a_usable_design(self):
        _,_,_,ctx,value=chain();value['sections'][1]['reader_progression']['marketing']['mechanism']=''
        with self.assertRaises(cq.ContentQualityError):bp.validate(value,ctx)

    def test_no_new_rhetorical_blacklist_or_automatic_emotion_guarantee(self):
        _,_,_,ctx,value=fixture();value['sections'][0]['reader_progression']['after']['emotion']='見落としへの懸念を意識し、必要な確認を行う気持ちを狙う'
        bp.validate(value,ctx)
        text=bp.render(value,ctx);self.assertIn('狙う感情の変化',text)
        self.assertNotIn('読者全員が安心した',text)

    def test_progression_failure_overrides_all_other_individual_passes(self):
        _,_,_,ctx,value=chain();rctx=bp.review_context(artifact(value,ctx));r=verdict(rctx)
        r['reader_progression_reviews']['s02']={'status':'answer_missing','reason':'次の問いに対する受け持ち章の説明が一致しない'}
        report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'全論点IDは存在'}]}
        bp.apply_review(report,r,rctx)
        self.assertFalse(report['valid']);self.assertEqual(report['blueprint_issues'][0]['section_ids'],['s02'])
        self.assertEqual(report['blueprint_issues'][0]['repair_kind'],'editorial')

    def test_missing_invalid_and_unknown_section_review_cannot_approve(self):
        _,_,_,ctx,value=chain();rctx=bp.review_context(artifact(value,ctx))
        for change in ('missing','extra','empty','unknown'):
            r=verdict(rctx)
            if change=='missing':r['reader_progression_reviews'].pop('s02')
            elif change=='extra':r['reader_progression_reviews']['s99']={'status':'pass','reason':'仮'}
            elif change=='empty':r['reader_progression_reviews']['s02']['reason']=''
            else:r['reader_progression_reviews']['s02']['status']='skip'
            with self.assertRaises(cq.ContentQualityError):bp.checked_review(r,rctx)

    def test_source_gap_routes_to_source_without_buying_editorial_fix(self):
        _,_,_,ctx,value=chain();rctx=bp.review_context(artifact(value,ctx));r=verdict(rctx)
        r['reader_progression_reviews']['s02']={'status':'evidence_missing','reason':'期待の根拠に使った具体的効果の資料がない'}
        report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'一般確認'}]};bp.apply_review(report,r,rctx)
        self.assertTrue(report['evidence_pending']);self.assertEqual(report['blueprint_issues'][0]['repair_kind'],'source')

    def test_same_request_review_schema_covers_chapters_without_new_stage(self):
        _,_,_,ctx,value=chain();rctx=bp.review_context(artifact(value,ctx))
        config=bp.review_config(cq.audit_output_config(cq.CHECKS),rctx)
        props=config['format']['schema']['properties']['reader_progression_reviews']['properties']
        self.assertEqual(set(props),{'s01','s02','s03'})
        bp.validate_shape(verdict(rctx)['reader_progression_reviews'],config['format']['schema']['properties']['reader_progression_reviews'])

    def test_reader_change_and_marketing_change_invalidate_prior_review(self):
        _,_,_,ctx,value=chain();a=artifact(value,ctx);rctx=bp.review_context(a);r={**verdict(rctx),'blueprint_review_context':rctx}
        for change in ('emotion','marketing','next'):
            v=copy.deepcopy(value)
            if change=='emotion':v['sections'][0]['reader_progression']['after']['emotion']='別の狙い'
            elif change=='marketing':v['sections'][1]['reader_progression']['marketing']['mechanism']='別の説明'
            else:v['sections'][0]['reader_progression']['next_questions'][0]['reason']='別の接続理由'
            with self.assertRaises(cq.ContentQualityError):bp.require_review(r,artifact(v,ctx))

    def test_deleting_or_reordering_target_requires_repairing_question_links(self):
        _,_,_,ctx,value=chain()
        for data in ({'edits':[],'delete_ids':['s02'],'section_order':[]},
            {'edits':[],'delete_ids':[],'section_order':['s02','s01','s03']}):
            with self.assertRaises(cq.ContentQualityError):bp.apply_edits(value,data,ctx,structural=True,targets=['s01','s02'])
        changed=copy.deepcopy(value['sections'][0]);changed['reader_progression']['next_questions'][0]['target_section_id']='s03'
        candidate=bp.apply_edits(value,{'edits':[{'id':'s01','section':changed}],'delete_ids':['s02'],'section_order':[]},ctx,structural=True,targets=['s01','s02'])
        bp.require_projection(artifact(candidate,ctx),ctx)

    def test_old_flat_reader_fields_are_not_silently_promoted(self):
        _,_,_,ctx,value=fixture();v=copy.deepcopy(value);v['sections'][0].pop('reader_progression');v['sections'][0].update(after_reading='旧式',reader_state='',transition='')
        with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)
        a=artifact(value,ctx);a['meta']['blueprint_version']='editorial-blueprint-v1'
        with self.assertRaises(cq.ContentQualityError):bp.require_projection(a)
        self.assertTrue(a['content_text'])  # Stored Markdown remains available to display.

    def test_real_research_audit_preserves_progression_result_in_both_routes(self):
        _,pages,matrix,ctx,value=chain();a=artifact(value,ctx);rctx=bp.review_context(a)
        for provider,routing in (('sonnet','legacy'),('tiered','focused')):
            checks=[{'key':k,'status':'not_applicable' if k in ('prose_quality','redundancy','comparison_conditions','metric_scope') else 'pass',
                'reason':'合成確認','requires_source_check':False,'affected_blocks':[],'research_ids':[],'source_urls':[]} for k in cq.CHECKS]
            result={**verdict(rctx),'checks':checks}
            result['reader_progression_reviews']['s02']={'status':'answer_missing','reason':'狙う期待と説明する利用場面が結びついていない'}
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':provider,'QUALITY_RESEARCH_ROUTING':routing}))
                send=stack.enter_context(patch.object(cq,'create_with_retry',return_value=response(result)))
                tiered=stack.enter_context(patch('pipeline.tiered_research.checked_request',return_value=(result,SimpleNamespace(input_tokens=1,output_tokens=1))))
                report=cq.audit(None,stage='research',text=a['content_text'],outline=a['content_text'],facts='',contract={'required_sections':[]},
                    requirements={'editorial_design':rctx},sources=cq.source_evidence({'content_text':json.dumps(pages)}))
            self.assertFalse(report['valid']);self.assertEqual(report['reader_progression_reviews'],result['reader_progression_reviews'])
            self.assertEqual(send.call_count+tiered.call_count,1)

    def test_generation_schema_covers_nullable_children_and_nested_outcome(self):
        _,_,_,ctx,value=chain()
        bp.validate_shape(value,bp.OUTPUT_CONFIG['format']['schema'])
        self.assertNotIn('after_reading',bp.SECTION_SCHEMA['properties'])
        self.assertNotIn('reader_state',bp.SECTION_SCHEMA['properties'])
        self.assertNotIn('transition',bp.SECTION_SCHEMA['properties'])

    def test_repair_targets_include_directly_connected_chapters_only(self):
        _,_,_,ctx,value=chain()
        unrelated=copy.deepcopy(value['sections'][-1]);unrelated.update(id='s04',title='別の補助説明');value['sections'].append(unrelated)
        report={'checks':[],'blueprint_issues':[{'section_ids':['s02']}]}
        self.assertEqual(bp.repair_targets(artifact(value,ctx),report),['s01','s02','s03'])

    def test_actual_local_repair_keeps_progression_and_requires_new_review(self):
        plan,_,matrix,ctx,value=chain();a=artifact(value,ctx);edited=copy.deepcopy(value['sections'][1])
        edited['reader_progression']['marketing']['mechanism']='閲覧と送信を使う具体的な場面から、選べる価値を説明する'
        report={'checks':[{'key':'coverage','status':'fail','reason':'狙う変化と説明をつなげる'}],
            'blueprint_issues':[{'repair_kind':'editorial','section_ids':['s02']}]}
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.db.get_job',return_value={}))
            stack.enter_context(patch('pipeline.db.get_artifact',return_value={'content_text':'{"required_sections":[]}'}))
            save=stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=lambda **kw:kw))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled',return_value=False))
            stack.enter_context(patch('pipeline.ai.create_with_retry',return_value=response({'edits':[{'id':'s02','section':edited}],'delete_ids':[],'section_order':[]})))
            result=bp.repair('job','条件',report,a,None)
        bp.require_projection(result,ctx)
        self.assertEqual(result['meta']['editorial_blueprint']['sections'][0],value['sections'][0])
        self.assertIn('具体的な場面',result['content_text'])
        self.assertFalse(save.call_args_list[-2].kwargs['meta']['valid'])

    def test_actual_outline_request_and_saved_projection_include_marketing_chain(self):
        from pipeline import step_outline
        plan,pages,matrix,ctx,value=chain()
        artifacts={'serp':{'content_text':'{}'},'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}},
            'fact_sheet':{'content_text':'facts'},'fresh_sources':{'content_text':json.dumps(pages)},'content_contract':{'content_text':'{"required_sections":[]}'}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(step_outline,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_outline,'get_job',return_value={}))
            stack.enter_context(patch.object(step_outline.anthropic,'Anthropic'))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            send=stack.enter_context(patch.object(step_outline,'create_with_retry',return_value=response(value)))
            stack.enter_context(patch.object(step_outline,'upsert_artifact',side_effect=lambda **kw:{'id':'saved',**kw}))
            result=step_outline.run('job','条件')
        request=send.call_args.kwargs
        self.assertIn('reader_progression',request['output_config']['format']['schema']['properties']['sections']['items']['properties'])
        self.assertIn('マーケティング目的',request['messages'][0]['content'])
        bp.require_projection(result,ctx);self.assertIn('次の問い',result['content_text']);self.assertIn('記事目的への役割',result['content_text'])

    def test_actual_writer_receives_chapter_change_and_marketing_chain(self):
        from pipeline import step_article,research_requirements as req
        plan,pages,matrix,ctx,value=chain();a=artifact(value,ctx);rctx=bp.review_context(a)
        facts=req.accepted_facts(matrix,plan);sources=cq.source_evidence({'content_text':json.dumps(pages)})
        requirements={'keyword':'条件','research_plan':plan,'research_decisions':matrix}
        audited={**requirements,'editorial_design':rctx};contract={'required_sections':[]}
        readiness={**verdict(rctx),'blueprint_review_context':rctx,'valid':True,'stage':'research','policy_version':cq.POLICY_VERSION,
            'checks':[{'key':k,'status':'pass','reason':'合成確認'} for k in cq.CHECKS],
            'snapshot':cq.snapshot(a['content_text'],cq.confirmed_facts(facts),a['content_text'],contract,audited,sources)}
        artifacts={'outline':a,'fact_sheet':{'content_text':facts},'fresh_sources':{'content_text':json.dumps(pages)},
            'content_contract':{'content_text':json.dumps(contract)},'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}},
            'research_validation':{'content_text':json.dumps(readiness)}}
        class Captured(Exception):pass
        with ExitStack() as stack:
            stack.enter_context(patch.object(step_article,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_article,'get_job',return_value={'id':'job'}))
            stack.enter_context(patch.object(step_article,'intent_review_requirements',return_value=copy.deepcopy(requirements)))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            stack.enter_context(patch.object(step_article.anthropic,'Anthropic'))
            writer=stack.enter_context(patch.object(step_article,'_call',side_effect=Captured()))
            with self.assertRaises(Captured):step_article.run('job','条件')
        messages=step_article.SYSTEM_PROMPT+json.dumps(writer.call_args.args[1:],ensure_ascii=False)+json.dumps(writer.call_args.kwargs,ensure_ascii=False)
        for term in ('この章で解消する疑問','狙う感情の変化','次の問い','記事目的への役割','潜在課題','選択の迷い'):
            self.assertIn(term,messages)
        self.assertEqual(writer.call_count,1)
