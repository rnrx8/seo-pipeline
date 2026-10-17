import copy
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import editorial_blueprint as bp, content_quality as cq
from pipeline import step_outline,step_research_guard as guard,step_structure_guard,step_article
from pipeline.outline_policy import current_policy
from test_claim_evidence import fixture as claim_fixture
from test_reader_value_flow import plan_fixture,response
from pipeline import research_requirements as req


def fixture():
    plan=plan_fixture();old,pages,matrix=claim_fixture()
    plan['items']=old['items']
    plan['reader_decisions'][0]['question']='閲覧と送信の条件から利用を判断できるか'
    plan['reader_decisions'][0]['answer_requirements']=['閲覧・送信の適用プランを区別する','自分の使い方に合う条件を判断できる']
    second=copy.deepcopy(matrix['items'][0]['claims'][0])
    second.update(text='送信はプレミアムのみです。',operation='送信',conditions=[{'dimension':'plan','value':'プレミアム'}],evidence_indices=[1])
    matrix['items'][0]['claims'].append(second)
    req.validate_matrix(matrix,plan,pages);matrix.update(valid=True,policy_sha256=req.matrix_policy())
    ctx=bp.context(plan,matrix)
    section=dict(id='s01',parent_id='',level=2,title='閲覧と送信の利用条件',need_ids=['i01'],decision_ids=['i01'],research_ids=['q1'],
        question='閲覧と送信にはどのプランが必要か',conclusion='見る用途と送る用途で必要プランを分けて判断する',
        answer_elements=[{'instruction':'閲覧と送信の適用プランを比較し、利用したい動作から選ぶ','claim_ids':['q1.c01','q1.c02']}],
        reader_progression={'before':{'understanding':'利用したい動作は決まっている','open_questions':['どのプランが必要か'],'emotion':''},
            'after':{'resolved_questions':['閲覧と送信にはどのプランが必要か'],'understanding':'閲覧と送信で適用条件が異なる',
                'decision':'自分の用途に必要なプランを選べる','action':'','emotion':''},'next_questions':[],'marketing':None},example='',
        format='比較表',items=['利用したい動作','適用プラン'],cta='',budget={'chars':600,'importance':5,'reason':'二つの動作と条件を比較する'})
    value={'titles':['閲覧と送信の利用条件を比較'],'lead_direction':'知りたい条件と記事で分かることを簡潔に示す',
           'sections':[section],'omissions':[]}
    return plan,pages,matrix,ctx,value


def artifact(value,ctx):
    return {'id':'outline-id','content_text':bp.render(value,ctx),'meta':{**bp.metadata(value,ctx),'editorial_policy':current_policy(),'length_origin':'reference'}}


def verdict(ctx, *, missing=None,kind='answer_missing'):
    rows={}
    for d in ctx['reader_decisions']:
        rows[d['id']]={'requirements':{str(i):{'status':kind if missing==i else 'pass','reason':'動作と実条件を照合した具体的説明',
            'section_ids':['s01']} for i in range(len(d['answer_requirements']))},
            'order':{'status':'pass','reason':'用途の回答が商品紹介に先行','section_ids':['s01']}}
    return {'blueprint_reviews':rows,'reader_progression_reviews':{sid:{'status':'pass','reason':'この章の回答範囲・変化・接続を合成入力で照合'} for sid in ctx.get('progression_section_ids',[])}}


class BlueprintTests(unittest.TestCase):
    def test_claims_conditions_sources_trace_to_real_sections(self):
        _,_,_,ctx,v=fixture();bp.validate(v,ctx);saved=artifact(v,ctx)
        self.assertEqual(bp.require_projection(saved,ctx),v)
        self.assertIn('s01',saved['content_text']);self.assertIn('スタンダード',saved['content_text']);self.assertIn('プレミアム',saved['content_text'])
        self.assertEqual(saved['meta']['reader_trace'][0]['claim_ids'],['q1.c01','q1.c02'])
        self.assertIn('https://a.example/faq',saved['content_text'])
        self.assertNotIn('quote',ctx['sources']['ref1'])

    def test_markdown_and_trace_are_not_independent_sources(self):
        _,_,_,ctx,v=fixture()
        for mutate in (lambda a:a.update(content_text=a['content_text']+'未承認の条件'),
            lambda a:a['meta']['reader_trace'][0].update(claim_ids=[])):
            a=artifact(v,ctx);mutate(a)
            with self.assertRaises(cq.ContentQualityError):bp.require_projection(a)

    def test_changed_reader_need_or_claim_invalidates_projection(self):
        _,_,_,ctx,v=fixture();a=artifact(v,ctx)
        for key in ('reader_decisions','accepted_claims'):
            changed=copy.deepcopy(ctx)
            if key=='reader_decisions':changed[key][0]['answer_requirements'].append('変更後の問い')
            else:changed[key]['q1.c01']['conditions'][0]['value']='別の対象'
            with self.assertRaises(cq.ContentQualityError):bp.require_projection(a,changed)

    def test_context_fingerprint_cannot_be_detached_from_stored_inputs(self):
        _,_,_,ctx,v=fixture();a=artifact(v,ctx);a['meta']['blueprint_context']['accepted_claims']={}
        with self.assertRaises(cq.ContentQualityError):bp.require_projection(a)

    def test_unknown_withheld_or_wrong_question_claim_fails(self):
        _,_,_,ctx,v=fixture()
        for cid in ('q1.c99','q2.c01'):
            changed=copy.deepcopy(v);changed['sections'][0]['answer_elements'][0]['claim_ids']=[cid]
            with self.assertRaises(cq.ContentQualityError):bp.validate(changed,ctx)
        ctx['accepted_claims']['q1.c01']['research_id']='q2'
        with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)

    def test_no_mandatory_emotion_example_or_transition_for_every_h3(self):
        _,_,_,ctx,v=fixture();child=copy.deepcopy(v['sections'][0]);child.update(id='s02',parent_id='s01',level=3,
            title='動作からプランを選ぶ',reader_progression=None,format='文章',items=[],budget={'chars':0,'importance':0,'reason':''})
        v['sections'].append(child);bp.validate(v,ctx)
        self.assertNotIn('この時点の読者心理',bp.render(v,ctx))

    def test_optional_example_has_illustrative_label(self):
        _,_,_,ctx,v=fixture();v['sections'][0]['example']='まず候補を閲覧し、連絡したい段階で送信条件を見る場面'
        self.assertIn('説明用の仮の具体例',bp.render(v,ctx))

    def test_invalid_parent_duplicate_ids_or_child_budget_fails(self):
        _,_,_,ctx,v=fixture()
        for edit in ({'id':'s01'},{'id':'s02','parent_id':'missing','level':3},{'id':'s02','parent_id':'s01','level':3}):
            changed=copy.deepcopy(v);child=copy.deepcopy(v['sections'][0]);child.update(edit);changed['sections'].append(child)
            with self.assertRaises(cq.ContentQualityError):bp.validate(changed,ctx)

    def test_empty_specific_design_fields_fail(self):
        _,_,_,ctx,v=fixture()
        for field in ('question','conclusion'):
            changed=copy.deepcopy(v);changed['sections'][0][field]=''
            with self.assertRaises(cq.ContentQualityError):bp.validate(changed,ctx)

    def test_unknown_need_or_disconnected_decision_fails(self):
        _,_,_,ctx,v=fixture()
        for field in ('need_ids','decision_ids','research_ids'):
            changed=copy.deepcopy(v);changed['sections'][0][field]=['unknown']
            with self.assertRaises(cq.ContentQualityError):bp.validate(changed,ctx)

    def test_important_omission_needs_reason_but_supporting_needs_no_listing(self):
        _,_,_,ctx,v=fixture();ctx['topics'].append({'id':'q2','priority':'important'})
        with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)
        v['omissions']=[{'research_id':'q2','reason':'補助統計がなくても動作と条件を判断できる'}];bp.validate(v,ctx)
        ctx['topics'].append({'id':'q3','priority':'supporting'});bp.validate(v,ctx)

    def test_required_topic_cannot_be_omitted(self):
        _,_,_,ctx,v=fixture();v['sections'][0].update(research_ids=[],decision_ids=[],need_ids=[])
        v['sections'][0]['answer_elements'][0]['claim_ids']=[];v['omissions']=[{'research_id':'q1','reason':'文字数削減'}]
        with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)

    def test_deferred_or_omitted_cta_does_not_reappear(self):
        _,_,_,ctx,v=fixture();v['sections'][0]['cta']='申込みを案内する'
        for role in ('omit','defer'):
            ctx['reader_design']['product_fit']['cta_role']=role
            with self.assertRaises(cq.ContentQualityError):bp.validate(v,ctx)
        ctx['reader_design']['product_fit']['cta_role']='appropriate';bp.validate(v,ctx)

    def test_competitor_reference_does_not_pad_content_based_budget(self):
        _,_,_,ctx,v=fixture();text=bp.render(v,ctx)
        self.assertIn('600字',text)
        self.assertEqual(step_outline.ensure_complete_volume_design(text,'14000字',reference_only=True),(text,False))

    def test_legacy_accepted_answer_gets_explicit_compatibility_id(self):
        from test_evidence_policy import fixture as old_fixture
        plan,pages,matrix=old_fixture();req.validate_matrix(matrix,plan,pages);matrix.update(valid=True,policy_sha256=req.matrix_policy())
        ctx=bp.context(plan,matrix)
        self.assertIn('q1.answer',ctx['accepted_claims']);self.assertIn('2025年1月時点',json.dumps(ctx,ensure_ascii=False))

    def test_model_cannot_insert_unknown_top_level_or_boolean_budget(self):
        _,_,_,ctx,v=fixture()
        for edit in ('extra','budget'):
            c=copy.deepcopy(v)
            if edit=='extra':c['valid']=True
            else:c['sections'][0]['budget']['chars']=True
            with self.assertRaises(cq.ContentQualityError):bp.validate(c,ctx)

    def test_individual_required_answer_failure_overrides_general_pass(self):
        _,_,_,ctx,v=fixture();ctx=bp.review_context(artifact(v,ctx));report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'IDは全件存在'}]}
        bp.apply_review(report,verdict(ctx,missing=1),ctx)
        self.assertFalse(report['valid']);self.assertEqual(report['checks'][0]['status'],'fail')
        self.assertEqual(report['blueprint_issues'][0]['repair_kind'],'editorial')

    def test_evidence_gap_is_distinct_from_answer_design_gap(self):
        _,_,_,ctx,v=fixture();ctx=bp.review_context(artifact(v,ctx));report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'一般確認'}]}
        bp.apply_review(report,verdict(ctx,missing=0,kind='evidence_missing'),ctx)
        self.assertTrue(report['evidence_pending']);self.assertEqual(report['blueprint_issues'][0]['repair_kind'],'source')

    def test_order_failure_cannot_be_hidden_by_all_answer_elements_pass(self):
        _,_,_,ctx,v=fixture();ctx=bp.review_context(artifact(v,ctx));value=verdict(ctx)
        value['blueprint_reviews']['i01']['order'].update(status='fail',reason='詳細回答前に推薦へ移っている')
        self.assertEqual(bp.checked_review(value,ctx)[0]['requirement_index'],'order')

    def test_missing_review_unknown_section_or_omitted_primary_is_rejected(self):
        _,_,_,ctx,v=fixture();ctx=bp.review_context(artifact(v,ctx))
        for edit in ('missing','unknown','omitted','empty'):
            r=verdict(ctx)
            if edit=='missing':r['blueprint_reviews']['i01']['requirements'].pop('1')
            elif edit=='unknown':r['blueprint_reviews']['i01']['requirements']['0']['section_ids']=['s99']
            elif edit=='omitted':r['blueprint_reviews']['i01']['requirements']['0']['status']='omitted'
            else:r['blueprint_reviews']['i01']['requirements']['0']['section_ids']=[]
            with self.assertRaises(cq.ContentQualityError):bp.checked_review(r,ctx)

    def test_supporting_can_be_omitted_without_blocking_main_answer(self):
        _,_,_,ctx,v=fixture();ctx=bp.review_context(artifact(v,ctx));d=copy.deepcopy(ctx['reader_decisions'][0]);d.update(id='i02',role='supporting');ctx['reader_decisions'].append(d)
        r=verdict(ctx)
        for item in r['blueprint_reviews']['i02']['requirements'].values():item.update(status='omitted',reason='主要判断には不要な補足',section_ids=[])
        self.assertFalse(bp.checked_review(r,ctx))

    def test_current_blueprint_requires_matching_semantic_review_before_writing(self):
        _,_,_,ctx,v=fixture();a=artifact(v,ctx);review_ctx=bp.review_context(a)
        with self.assertRaises(cq.ContentQualityError):bp.require_review({'valid':True},a)
        r={**verdict(review_ctx),'blueprint_review_context':review_ctx};bp.require_review(r,a)
        r['blueprint_review_context']['inputs_sha256']='old'
        with self.assertRaises(cq.ContentQualityError):bp.require_review(r,a)

    def test_local_edit_preserves_other_sections_and_revalidates_projection(self):
        _,_,_,ctx,v=fixture();v['sections'].append({**copy.deepcopy(v['sections'][0]),'id':'s02','title':'判断のまとめ'})
        edit=copy.deepcopy(v['sections'][0]);edit['reader_progression']['after']['decision']='閲覧と送信を分けて判断できる'
        changed=bp.apply_edits(v,{'edits':[{'id':'s01','section':edit}],'delete_ids':[],'section_order':[]},ctx,structural=False,targets=['s01'])
        self.assertEqual(changed['sections'][1],v['sections'][1]);bp.require_projection(artifact(changed,ctx),ctx)

    def test_unrelated_section_or_structure_change_cannot_be_patched(self):
        _,_,_,ctx,v=fixture();edit=copy.deepcopy(v['sections'][0]);edit['title']='別の問い'
        response={'edits':[{'id':'s01','section':edit}],'delete_ids':[],'section_order':[]}
        with self.assertRaises(cq.ContentQualityError):bp.apply_edits(v,response,ctx,structural=False,targets=['s01'])
        with self.assertRaises(cq.ContentQualityError):bp.apply_edits(v,response,ctx,structural=True,targets=['s02'])

    def test_targeted_structure_edit_keeps_required_answers(self):
        _,_,_,ctx,v=fixture();response={'edits':[],'delete_ids':['s01'],'section_order':[]}
        with self.assertRaises(cq.ContentQualityError):bp.apply_edits(v,response,ctx,structural=True,targets=['s01'])

    def test_actual_structure_guard_does_not_inject_markdown_behind_blueprint(self):
        _,_,_,ctx,v=fixture();a=artifact(v,ctx);contract={'required_sections':[{'key':'named_service_comparison','protected':True,'candidate_services':['A','B'],'minimum_named_items':2}]}
        with patch.object(step_structure_guard,'get_artifact',side_effect=lambda j,k:a if k=='outline' else {'content_text':json.dumps(contract)}),patch.object(step_structure_guard,'upsert_artifact',side_effect=lambda **kw:kw) as save,patch.object(step_structure_guard,'repair_outline',side_effect=AssertionError('must not inject')):
            result=step_structure_guard.run('job','比較')
        self.assertEqual(save.call_count,1);self.assertEqual(result['step'],'structure_validation');self.assertFalse(result['meta']['valid'])

    def test_actual_outline_generation_saves_canonical_and_deterministic_markdown(self):
        plan,pages,matrix,ctx,v=fixture();artifacts={'serp':{'content_text':'{}'},'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}},
            'fact_sheet':{'content_text':'facts'},'fresh_sources':{'content_text':json.dumps(pages)},'content_contract':{'content_text':json.dumps({'required_sections':[]})}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(step_outline,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_outline,'get_job',return_value={}))
            stack.enter_context(patch.object(step_outline.anthropic,'Anthropic'))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            send=stack.enter_context(patch.object(step_outline,'create_with_retry',return_value=response(v)))
            stack.enter_context(patch.object(step_outline,'upsert_artifact',side_effect=lambda **kw:{'id':'saved',**kw}))
            saved=step_outline.run('job','条件')
        self.assertIn('output_config',send.call_args.kwargs);self.assertIn('accepted_claims',send.call_args.kwargs['messages'][0]['content'])
        bp.require_projection(saved,ctx);self.assertEqual(saved['content_text'],bp.render(v,ctx))

    def test_actual_research_audit_records_individual_results_in_same_request(self):
        plan,pages,matrix,ctx,v=fixture();a=artifact(v,ctx);rctx=bp.review_context(a)
        checks=[{'key':k,'status':'not_applicable' if k in ('prose_quality','redundancy','comparison_conditions','metric_scope') else 'pass','reason':'合成された確認結果'} for k in cq.CHECKS]
        result={**verdict(rctx,missing=1),'checks':checks}
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','QUALITY_RESEARCH_ROUTING':'legacy'}),patch.object(cq,'create_with_retry',return_value=response(result)) as send:
            report=cq.audit(None,stage='research',text=a['content_text'],outline=a['content_text'],facts='',contract={'required_sections':[]},requirements={'editorial_design':rctx,'research_plan':plan},sources='')
        self.assertEqual(send.call_count,1);self.assertFalse(report['valid']);self.assertEqual(report['blueprint_issues'][0]['requirement_index'],'1')
        self.assertIn('blueprint_reviews',send.call_args.kwargs['output_config']['format']['schema']['required'])

    def test_actual_blueprint_repair_regenerates_projection_and_invalidates_review(self):
        plan,pages,matrix,ctx,v=fixture();a=artifact(v,ctx);edit=copy.deepcopy(v['sections'][0]);edit['conclusion']='閲覧と送信で必要なプランを明確に分ける'
        report={'checks':[{'key':'coverage','status':'fail','reason':'結論を具体化'}],
            'blueprint_issues':[{'repair_kind':'editorial','section_ids':['s01']}]}
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.db.get_artifact',return_value={'content_text':'{"required_sections":[]}'}))
            stack.enter_context(patch('pipeline.db.get_job',return_value={}))
            save=stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=lambda **kw:kw))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled',return_value=False))
            stack.enter_context(patch('pipeline.ai.create_with_retry',return_value=response({'edits':[{'id':'s01','section':edit}],'delete_ids':[],'section_order':[]})))
            result=bp.repair('job','条件',report,a,None)
        bp.require_projection(result,ctx);self.assertFalse(result['meta']['audited'])
        self.assertEqual(save.call_args_list[-2].kwargs['meta'],{'valid':False})

    def test_source_gap_repair_does_not_send_model_request(self):
        plan,_,matrix,ctx,v=fixture()
        with patch('pipeline.research_requirements.require_matrix',return_value=matrix),patch('pipeline.research_requirements.load_plan',return_value=plan),patch('pipeline.ai.create_with_retry') as send:
            with self.assertRaises(cq.ContentQualityError):bp.repair('job','条件',{'checks':[],'evidence_pending':True},artifact(v,ctx),None)
        send.assert_not_called()

    def test_volume_check_constrains_only_explicit_user_setting(self):
        _,_,_,ctx,v=fixture()
        self.assertEqual(bp.budget_issues(v,None),[])
        self.assertEqual(bp.budget_issues(v,'800字以内'),[])
        self.assertEqual(bp.budget_issues(v,'500字以内')[0]['repair_kind'],'volume')
        self.assertTrue(bp.budget_issues(v,'5000字'))

    def test_tiered_focused_audit_keeps_individual_design_result_after_routing(self):
        plan,pages,matrix,ctx,v=fixture();a=artifact(v,ctx);rctx=bp.review_context(a)
        checks=[{'key':k,'status':'not_applicable' if k in ('prose_quality','redundancy','comparison_conditions','metric_scope') else 'pass',
            'reason':'採用事実と一致する合成確認','requires_source_check':False,'affected_blocks':[],'research_ids':[],'source_urls':[]} for k in cq.CHECKS]
        value={**verdict(rctx,missing=0,kind='evidence_missing'),'checks':checks}
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}),patch('pipeline.tiered_research.checked_request',return_value=(value,SimpleNamespace(input_tokens=1,output_tokens=1))) as send:
            report=cq.audit(None,stage='research',text=a['content_text'],outline=a['content_text'],facts='',contract={'required_sections':[]},
                requirements={'editorial_design':rctx,'research_plan':plan,'research_decisions':matrix},sources=cq.source_evidence({'content_text':json.dumps(pages)}))
        self.assertEqual(send.call_count,1);self.assertFalse(report['valid']);self.assertTrue(report['evidence_pending'])

    def test_placement_empty_is_preserved_through_actual_service_map_and_cta_inject(self):
        from pipeline import step_service_map,step_cta_inject
        plan,pages,matrix,ctx,v=fixture();a=artifact(v,ctx);rctx=bp.review_context(a)
        readiness={**verdict(rctx),'blueprint_review_context':rctx}
        with patch.object(step_service_map,'get_artifact',side_effect=lambda j,k:a if k=='outline' else {'content_text':json.dumps(readiness)}),patch('pipeline.research_requirements.load_plan',return_value=plan),patch('pipeline.research_requirements.require_matrix',return_value=matrix),patch.object(step_service_map,'upsert_artifact',side_effect=lambda **kw:kw),patch.object(step_service_map,'create_with_retry') as send:
            sm=step_service_map.run('job','条件')
        send.assert_not_called();self.assertEqual(json.loads(sm['content_text'])['cta_after_h2'],[])
        article={'content_text':'## 比較\n選び方\n## まとめ\n要点','meta':{}}
        with patch.object(step_cta_inject,'get_artifact',side_effect=lambda j,k:article if k=='article' else sm),patch.object(step_cta_inject,'get_job',return_value={'cta_id':'cta'}),patch.object(step_cta_inject,'get_cta_by_id',return_value={'body':'登録する','url':'https://a.example'}),patch('pipeline.db.get_optional_artifact',return_value=a),patch.object(step_cta_inject,'upsert_artifact') as save:
            result=step_cta_inject.run('job','比較')
        self.assertEqual(result,article);save.assert_not_called()

    def test_child_cta_resolves_to_actual_parent_without_a_new_model_decision(self):
        _,_,_,ctx,v=fixture();ctx['reader_design']['product_fit']['cta_role']='appropriate'
        child=copy.deepcopy(v['sections'][0]);child.update(id='s02',level=3,parent_id='s01',title='利用条件に合う人',cta='閲覧から始めたい場合に案内する',budget={'chars':0,'importance':0,'reason':''})
        v['sections'].append(child)
        self.assertEqual(bp.placement(artifact(v,ctx))['cta_after_h2'],[v['sections'][0]['title']])

    @patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','QUALITY_RESEARCH_ROUTING':'legacy'})
    def test_guard_review_reaches_actual_writer_with_same_snapshot_and_no_paid_call(self):
        plan,pages,matrix,ctx,v=fixture();v['sections'][0]['title']='閲覧と送信｜利用条件の比較';a=artifact(v,ctx);contract={'required_sections':[]};job={'id':'job'}
        base_requirements={'research_plan':plan,'research_decisions':matrix,'keyword':'条件'}
        rctx=bp.review_context(a);checks=[{'key':k,'status':'not_applicable' if k in ('prose_quality','redundancy','comparison_conditions','metric_scope') else 'pass','reason':'合成試験で回答を確認'} for k in cq.CHECKS]
        artifacts={'outline':a,'fact_sheet':{'content_text':req.accepted_facts(matrix,plan)},
            'fresh_sources':{'content_text':json.dumps(pages)},'content_contract':{'content_text':json.dumps(contract)},
            'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}}}
        def save(**kw):artifacts[kw['step']]={'id':'saved',**kw};return artifacts[kw['step']]
        class Captured(Exception):pass
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','QUALITY_RESEARCH_ROUTING':'legacy'}))
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict'))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch.object(guard,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(guard,'get_job',return_value=job))
            stack.enter_context(patch.object(guard,'upsert_artifact',side_effect=save))
            stack.enter_context(patch.object(guard,'intent_review_requirements',return_value=copy.deepcopy(base_requirements)))
            stack.enter_context(patch.object(guard.anthropic,'Anthropic'))
            audit_send=stack.enter_context(patch.object(cq,'create_with_retry',return_value=response({**verdict(rctx),'checks':checks})))
            result=guard.run('job','条件')
            self.assertTrue(json.loads(result['content_text'])['valid']);self.assertEqual(audit_send.call_count,1)
            stack.enter_context(patch.object(step_article,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_article,'get_job',return_value=job))
            stack.enter_context(patch.object(step_article,'intent_review_requirements',return_value=copy.deepcopy(base_requirements)))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            writer=stack.enter_context(patch.object(step_article,'_call',side_effect=Captured()))
            stack.enter_context(patch.object(step_article,'_parse_volume_design',side_effect=AssertionError('Canonical blueprint budgets must not be reparsed from Markdown')))
            with self.assertRaises(Captured):step_article.run('job','条件')
            # Reader mapping can pass while a separate factual QA check fails.
            # Warn-and-continue must not start another paid writer for new designs.
            saved_readiness=copy.deepcopy(artifacts['research_validation'])
            failed=json.loads(saved_readiness['content_text']);failed['valid']=False
            next(c for c in failed['checks'] if c['key']=='evidence_support').update(status='fail',reason='unsupported ranking')
            artifacts['research_validation']={**saved_readiness,'content_text':json.dumps(failed)}
            with patch('pipeline.quality_warnings.enabled',return_value=True):
                with self.assertRaises(cq.ContentQualityError):step_article.run('job','条件')
            self.assertEqual(writer.call_count,1)
            artifacts['research_validation']=saved_readiness
            sent=json.dumps(writer.call_args.args[1:],ensure_ascii=False)+json.dumps(writer.call_args.kwargs,ensure_ascii=False)
            self.assertIn('この章で解消する疑問',sent);self.assertIn('q1.c02',sent);self.assertIn('プレミアム',sent)
            # A later change to the canonical design cannot reuse that readiness.
            changed=copy.deepcopy(v);changed['sections'][0]['reader_progression']['after']['decision']='変更した到達点'
            artifacts['outline']=artifact(changed,ctx)
            with self.assertRaises(cq.ContentQualityError):step_article.run('job','条件')
            self.assertEqual(writer.call_count,1)

    def test_same_inputs_and_ids_do_not_approve_changed_blueprint_even_in_warning_scope(self):
        _,_,_,ctx,v=fixture();a=artifact(v,ctx);rctx=bp.review_context(a);r={**verdict(rctx),'blueprint_review_context':rctx}
        changed=copy.deepcopy(v);changed['sections'][0]['answer_elements'][0]['instruction']='名前だけ並べる'
        from pipeline.quality_warnings import _CONTEXT
        token=_CONTEXT.set(('job','article'))
        try:
            with self.assertRaises(cq.ContentQualityError):bp.require_review(r,artifact(changed,ctx))
        finally:_CONTEXT.reset(token)

    def test_claim_conditions_and_authoring_limits_are_not_retyped_by_outline_model(self):
        plan,_,matrix,ctx,v=fixture()
        matrix['items'][0]['claim_limits']=['別動作へ無料条件を広げない']
        updated=bp.context(plan,matrix)
        self.assertIn('別動作へ無料条件を広げない',updated['accepted_claims']['q1.c01']['claim_limits'])
        self.assertNotIn('conditions',bp.SECTION_SCHEMA['properties'])
        self.assertNotIn('markdown',bp.SCHEMA['properties'])

    def test_method_comparison_assignment_alone_does_not_complete_choice(self):
        _,_,_,ctx,v=fixture();ctx['reader_decisions'][0].update(question='どの方法が自分に合うか',answer_requirements=['方法ごとの違いを示す','状況による選び分けを説明する'])
        v['sections'][0].update(question='どんな方法があるか',conclusion='方法Aと方法Bがある')
        v['sections'][0]['reader_progression']['after'].update(understanding='方法の名前が分かる',decision='')
        v['sections'][0]['answer_elements']=[{'instruction':'方法名を並べる','claim_ids':[]}]
        bp.validate(v,ctx)  # Structural validity is deliberately not semantic acceptance.
        rctx=bp.review_context(artifact(v,ctx));report={'valid':True,'checks':[{'key':'coverage','status':'pass','reason':'IDは割当て済み'}]}
        individual=verdict(rctx,missing=1);individual['blueprint_reviews']['i01']['requirements']['1']['reason']='どの状況ならどの方法を選ぶかが設計されていない'
        bp.apply_review(report,individual,rctx)
        self.assertFalse(report['valid']);self.assertIn('選ぶか',report['blueprint_issues'][0]['reason'])

    def test_contract_does_not_turn_method_choice_into_product_comparison(self):
        from pipeline.content_contract import build_content_contract
        plan=plan_fixture()
        contract=build_content_contract(keyword='探し方',intent_text=json.dumps(plan['reader_design'],ensure_ascii=False),
            query_attrs_text='{"searcher_stage":"比較検討"}',fact_text='',job={'article_purpose':'cv'},service={'name':'商品A'},
            candidate_services=['商品A','商品B'],reader_decisions=plan['reader_decisions'],reader_design=plan['reader_design'])
        self.assertIn('どの手段',contract['primary_intent'])
        self.assertNotIn('named_service_comparison',[s['key'] for s in contract['required_sections']])
        self.assertEqual(contract['service_treatment'],'integrated')

    def test_explicit_named_comparison_is_still_protected(self):
        from pipeline.content_contract import build_content_contract
        plan=plan_fixture()
        contract=build_content_contract(keyword='商品A 商品B 違い',intent_text='',query_attrs_text='{}',fact_text='',job={},
            candidate_services=['商品A','商品B','背景商品C'],reader_decisions=plan['reader_decisions'],reader_design=plan['reader_design'])
        section=next(s for s in contract['required_sections'] if s['key']=='named_service_comparison')
        self.assertEqual(section['candidate_services'],['商品A','商品B'])

    def test_unhelpful_registered_product_does_not_become_required_cv_coverage(self):
        from pipeline.content_contract import build_content_contract
        plan=plan_fixture();plan['reader_design']['product_fit']['role']='not_useful'
        contract=build_content_contract(keyword='手順',intent_text='',query_attrs_text='{}',fact_text='',job={'article_purpose':'cv'},service={'name':'商品A'},
            candidate_services=[],reader_decisions=plan['reader_decisions'],reader_design=plan['reader_design'])
        self.assertEqual(contract['service_treatment'],'none')
        self.assertNotIn('featured_service_coverage',[s['key'] for s in contract['required_sections']])
