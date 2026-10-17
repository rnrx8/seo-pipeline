import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import production_brief as pb, reader_needs as rn, db, step_intent as intent
from pipeline import research_requirements as req, research_plan_review as review
from pipeline import reader_decisions as rd
from pipeline.content_quality import ContentQualityError


def response(value, stop='end_turn'):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(text=json.dumps(value,ensure_ascii=False))],
                           usage=SimpleNamespace(input_tokens=1,output_tokens=1))


def fixtures():
    material={'version':pb.PRODUCT_VERSION,'tenant_id':'owner',
              'service':{'id':'service','name':'A','must_include':None,'must_exclude':None},
              'materials':[{'id':'service:point:0','text':'Webで相手を検索できる','origin':'registered_service'}]}
    brief={'version':pb.VERSION,'keyword':'探し方','product_input_sha256':pb.fingerprint(material),
           'request':{'article_purpose':'cv','target_audience':None},'product':material['service'],
           'cta':None,'registered_companies':[]}
    sources={'keyword':'探し方','serp':'手段ごとの違いを知りたい', 'request':json.dumps(brief['request'],ensure_ascii=False)}
    need=dict(id='i01',question='どの手段で探すと自分に合うか',reader_situation='初めて探す人を主対象とする仮説',
              background_emotion='選び方に迷う可能性',desired_state='自分に合う手段を選べる',barrier='手段の違いが分からない',
              answer_requirements=['各手段の特徴と向く状況を示す','違いを比較して選べる'],
              answer_order='手段の違いを先に示し、個別のサービスはその後',tone='実用的で決めつけない',
              example='',after_reading='自分の条件で次に試す手段を選べる',adoption_reason='検索への直接回答',
              confidence_reason='問いはSERP、生活背景は仮説',support=[
                  dict(field='question',kind='observed',source='serp',quote='手段ごとの違いを知りたい',reason='実際のSERP語句'),
                  *[dict(field=f,kind='hypothesis',source='keyword',quote='探し方',reason='問いからの仮説で全員には当てない')
                    for f in ('context','desired_state','barrier')]])
    understanding={'value_connections':[dict(situation='検索から相手を検討したい',desired_change='候補を見て選べる',
                       value='Web検索から候補を検討できるという資料上の価値',constraints='利用条件は調査前',
                       source_refs=[{'id':'service:point:0','quote':'Webで相手を検索できる'}])],
                   'maker_values':[],'unknowns':['料金条件はこの資料から確定できない']}
    value={'analysis':'## 検索意図\n手段の違いを説明してから商品選びへつなぐ。', 'selected':[need],
           'excluded':[{'question':'読者全員が同じ家庭事情を抱えるか','reason':'入力で確かめられず主要回答でもない'}],
           'product_fit':dict(role='conditional',reason='Webから探したい場合に限る候補',need_ids=['i01'],
                              material_ids=['service:point:0'],cta_role='omit'), 'product_understanding':understanding}
    return brief,material,sources,value


def plan_fixture():
    brief,_,sources,value=fixtures()
    needs=rn.record(value,brief=brief,intent_sources=sources)
    item=dict(id='q1',question='探す手段ごとの特徴と向く状況',subject='共通',priority='essential',
              priority_reason='直接回答の比較',required=True,source_requirement='general_guidance',
              requires_current=False,official_check_required=False)
    plan={'items':[item],'candidate_services':[],'scope_reason':'まず手段を判断する','content_gaps':[dict(need_id='i01',question=value['selected'][0]['question'],status='not_assessed',reason='競合本文は未取得',opportunity='手段の差を調べる',research_ids=['q1'],evidence=[])],
          'reader_decisions':[dict(id='i01',question=value['selected'][0]['question'],role='primary',scope='手段選択',
                                   depends_on=[],research_ids=['q1'],answer_requirements=value['selected'][0]['answer_requirements'])],
          'reader_design':rn.editorial_context(brief,needs)}
    return plan


class ReaderValueFlowTests(unittest.TestCase):
    def test_observation_and_hypothesis_keep_distinct_anchors(self):
        _,material,sources,value=fixtures()
        rn.validate(value,sources,product_material_ids=[m['id'] for m in material['materials']],has_product=True)
        self.assertEqual(value['selected'][0]['support'][1]['kind'],'hypothesis')

    def test_invented_observation_quote_is_rejected(self):
        _,_,sources,value=fixtures();value['selected'][0]['support'][0]['quote']='捏造した検索結果'
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True)

    def test_unknown_observation_source_is_rejected(self):
        _,_,sources,value=fixtures();value['selected'][0]['support'][0]['source']='made-up-survey'
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True)

    def test_unlabelled_scenario_is_not_a_reader_observation(self):
        _,_,sources,value=fixtures();value['selected'][0]['example']='仕事から帰って探す場面'
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True)

    def test_situation_with_no_hypothesis_or_source_record_is_rejected(self):
        _,_,sources,value=fixtures();value['selected'][0]['support']=[r for r in value['selected'][0]['support'] if r['field']!='context']
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True)

    def test_product_can_be_irrelevant_without_a_forced_cta(self):
        _,material,sources,value=fixtures();value['product_fit'].update(role='not_useful',reason='この操作には商品が不要',material_ids=[],need_ids=[],cta_role='omit')
        rn.validate(value,sources,product_material_ids=[m['id'] for m in material['materials']],has_product=True,has_cta=True)

    def test_pure_howto_needs_no_product_comparison_or_strong_emotion(self):
        _,_,sources,value=fixtures();n=value['selected'][0]
        n.update(question='操作の手順は何か',background_emotion='',barrier='',answer_requirements=['操作の順番を示す'])
        value['product_fit'].update(role='none',reason='商材指定なし',material_ids=[],need_ids=[])
        rn.validate(value,sources)

    def test_useful_product_needs_a_reader_and_material_connection(self):
        _,_,sources,value=fixtures();value['product_fit'].update(role='useful',need_ids=[],material_ids=[])
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True)

    def test_unrelated_cta_is_not_made_appropriate(self):
        _,_,sources,value=fixtures();value['product_fit'].update(role='not_useful',cta_role='appropriate')
        with self.assertRaises(ContentQualityError):rn.validate(value,sources,has_product=True,has_cta=True)

    def test_product_value_is_supplied_not_fact_verified(self):
        _,material,_,value=fixtures();product=pb.product_record(value['product_understanding'],material)
        self.assertEqual(product['fact_status'],'supplied_not_verified')
        product['fact_status']='verified'
        with self.assertRaises(ContentQualityError):pb.validate_product(product,material)

    def test_product_value_requires_matching_original_material(self):
        _,material,_,value=fixtures();value['product_understanding']['value_connections'][0]['source_refs'][0]['quote']='効果100%'
        with self.assertRaises(ContentQualityError):pb.product_record(value['product_understanding'],material)

    def test_changed_material_invalidates_the_product_cache(self):
        _,material,_,value=fixtures();product=pb.product_record(value['product_understanding'],material)
        changed=copy.deepcopy(material);changed['materials'][0]['text']+='。追加料金が必要'
        with self.assertRaises(ContentQualityError):pb.validate_product(product,changed)

    def test_raw_input_is_bounded_but_changed_hidden_tail_changes_the_hash(self):
        _,material,_,_=fixtures();material['materials'][0]['text']='x'*30000
        model=pb.model_product_input(material)
        self.assertTrue(model['materials'][0]['truncated']);self.assertEqual(len(model['materials'][0]['text']),12000)
        changed=copy.deepcopy(material);changed['materials'][0]['text']+='y'
        self.assertNotEqual(pb.fingerprint(changed),pb.fingerprint(material))

    def test_product_sources_are_deduplicated_and_order_stable(self):
        rows=[{'title':'A','content_text':'first'},{'title':'B','content_text':'second'}]
        self.assertEqual(pb.materials_for(None,rows+rows),pb.materials_for(None,list(reversed(rows))))

    def test_cache_query_scopes_the_owning_job_even_with_server_credentials(self):
        result={'content_text':'{}','meta':{'input_sha256':'hash'},'jobs':{'tenant_id':'owner'}}
        fake=SimpleNamespace(raise_for_status=lambda:None,json=lambda:[result])
        with patch.object(db.requests,'get',return_value=fake) as query:
            self.assertEqual(db.get_cached_product_context('owner','hash'),result)
            params=query.call_args.kwargs['params'];self.assertEqual(params['jobs.tenant_id'],'eq.owner')
            self.assertIn('!inner',params['select'])
        result['jobs']['tenant_id']='other'
        with patch.object(db.requests,'get',return_value=fake):
            with self.assertRaises(ValueError):db.get_cached_product_context('owner','hash')

    def test_cross_owner_product_registration_is_rejected_before_model_work(self):
        with patch.object(db,'get_job',return_value={'tenant_id':'owner','service_id':'s'}),patch.object(db,'get_service_by_id',return_value={'tenant_id':'other'}):
            with self.assertRaises(ContentQualityError):pb.prepare('j','keyword')

    def test_automatically_derived_length_does_not_change_reader_intent_input(self):
        job={'tenant_id':'owner','word_count_setting':None}
        with patch.object(db,'get_job',return_value=job):
            before,_,_,_=pb.prepare('j','keyword')
            job['word_count_setting']='4000字'
            after,_,_,_=pb.prepare('j','keyword')
        self.assertEqual(before,after)
        # The requested/deduced length remains in normal job settings and Stage 1.

    def test_old_reader_design_cannot_follow_a_new_serp(self):
        brief,_,sources,value=fixtures();saved={'content_text':json.dumps(rn.record(value,brief=brief,intent_sources=sources))}
        with patch.object(db,'get_optional_artifact',return_value=saved),patch.object(db,'get_artifact',return_value={'content_text':'new SERP'}):
            with self.assertRaises(ContentQualityError):rn.load('j',brief)

    def test_interrupted_analysis_is_not_a_legacy_reader_design(self):
        with patch.object(db,'get_optional_artifact',return_value={'content_text':json.dumps({'version':rn.VERSION,'status':'analyzing'})}):
            with self.assertRaises(ContentQualityError):rn.load('j')

    def test_plan_request_binds_the_adopted_questions_without_mutating_legacy_schema(self):
        original=copy.deepcopy(req.PLAN_SCHEMA)
        needs={'selected':[{'id':'i01','question':'方法をどう選ぶか'},{'id':'i02','question':'どう始めるか'}]}
        config=req.plan_output_schema({'reader_needs':needs})
        array=config['format']['schema']['properties']['reader_decisions']
        self.assertNotIn('maxItems',array)
        self.assertNotIn('minItems',array)
        self.assertIn('"minItems": 2',array['description'])
        self.assertIn('"maxItems": 2',array['description'])
        self.assertEqual(array['items']['properties']['id']['enum'],['i01','i02'])
        self.assertEqual(array['items']['properties']['question']['enum'],['方法をどう選ぶか','どう始めるか'])
        self.assertEqual(array['items']['properties']['depends_on']['items']['enum'],['i01','i02'])
        self.assertEqual(req.PLAN_SCHEMA,original)
        self.assertEqual(req.plan_output_schema({}),original)

    def test_selected_question_cannot_be_replaced_by_product_selection(self):
        plan=plan_fixture();plan['reader_decisions'][0]['question']='AとBのどちらのアプリを選ぶか'
        with self.assertRaises(ContentQualityError):req.validate_plan(plan)

    def test_repair_updates_research_links_and_preserves_intent_ids(self):
        plan=plan_fixture();updates=copy.deepcopy(plan['items']);updates[0]['question']='手段の違いと向く状況'
        changes={'updates':updates,'additions':[],'removals':[],'candidate_services':[],'scope_reason':'維持',
                 'reader_decisions':copy.deepcopy(plan['reader_decisions']),'content_gaps':copy.deepcopy(plan['content_gaps'])}
        result=req.apply_plan_repair(plan,changes)
        self.assertEqual(result['reader_decisions'][0]['id'],'i01')
        changes['reader_decisions'][0]['research_ids']=['deleted-q']
        with self.assertRaises(ContentQualityError):req.apply_plan_repair(plan,changes)

    def test_a_primary_answer_cannot_depend_on_a_product_recommendation(self):
        plan=plan_fixture();d=copy.deepcopy(plan['reader_decisions'][0]);d.update(id='i02',question='商品選び',role='downstream',depends_on=['i01'])
        plan['reader_decisions'].append(d);plan['reader_decisions'][0]['depends_on']=['i02']
        with self.assertRaises(ContentQualityError):rd.validate(plan['reader_decisions'],plan['items'])

    def test_existing_plan_review_detects_role_error_even_if_question_design_is_complete(self):
        plan=plan_fixture();verdict={'valid':True,'issues':[],
            'claim_checks':{'q1':{'answer_scope':'手段を選ぶ','source_requirement':'general_guidance','official_check_required':False}},
            'decision_reviews':{'i01':{'status':'pass','reason':'質問がある','missing_requirements':[],'research_ids':[]}},
            'decision_design_checks':{'i01':{'expected_role':'downstream','reason':'分類を独立に確認した対照例','unnecessary_requirements':[]}}}
        with patch.object(review,'create_with_retry',return_value=response(verdict)),patch.object(review,'upsert_artifact'):
            result,_=review.review('j',plan,{},1)
        self.assertFalse(result['valid']);self.assertIn('primary→downstream',result['issues'][0]['reason'])

    def test_reader_context_is_preserved_separately_from_accepted_facts(self):
        from pipeline.generation_context import outline_planning_context
        plan=plan_fixture();context=outline_planning_context(plan)
        self.assertEqual(context['reader_design']['reader_needs'][0]['after_reading'],plan['reader_design']['reader_needs'][0]['after_reading'])
        self.assertIn('仮説',context['reader_design']['fact_boundary']);self.assertNotIn('product_understanding',context)

    def test_analysis_plan_review_and_writer_packet_share_the_same_intent_and_answer(self):
        from pipeline.generation_context import decision_bundle
        brief,material,sources,value=fixtures();records={}
        plan=plan_fixture();plan.pop('reader_design')
        verdict={'valid':True,'issues':[],
            'claim_checks':{'q1':{'answer_scope':'手段を選ぶ','source_requirement':'general_guidance','official_check_required':False}},
            'decision_reviews':{'i01':{'status':'pass','reason':'質問が必要な回答を調べる','missing_requirements':[],'research_ids':[]}},
            'decision_design_checks':{'i01':{'expected_role':'primary','reason':'検索への直接回答','unnecessary_requirements':[]}}}
        def save(**data):
            records[data['step']]={**data,'id':data['step']};return records[data['step']]
        with patch.object(intent,'prepare',return_value=(brief,material,None,'miss')),\
             patch.object(intent,'get_artifact',return_value={'content_text':sources['serp']}),\
             patch.object(intent.anthropic,'Anthropic'),patch.object(intent,'upsert_artifact',side_effect=save),\
             patch.object(intent,'create_with_retry',side_effect=[response({}),response(value)]):
            intent.run('j',brief['keyword'])
        records['serp']={'content_text':sources['serp']}
        with patch.object(req,'get_job',return_value={'tenant_id':'owner'}),\
             patch.object(req,'get_artifact',side_effect=lambda j,s:records[s]),\
             patch.object(req,'get_optional_artifact',side_effect=lambda j,s:records.get(s)),\
             patch.object(db,'get_artifact',side_effect=lambda j,s:records[s]),\
             patch.object(db,'get_optional_artifact',side_effect=lambda j,s:records.get(s)),\
             patch.object(pb,'prepare',return_value=(brief,material,None,'miss')),\
             patch.object(req,'create_with_retry',return_value=response(plan)),\
             patch.object(review,'create_with_retry',return_value=response(verdict)),\
             patch.object(review,'upsert_artifact',side_effect=save),\
             patch.object(req,'upsert_artifact',side_effect=save):
            req.plan('j',brief['keyword'])
        accepted=json.loads(records['research_plan']['content_text'])
        matrix={'valid':True,'policy_sha256':req.matrix_policy(),'items':[dict(id='q1',verified=True,status='confirmed',
            basis='guidance',answer='手段ごとの特徴と向く状況を比較する',publication_conditions=[],claim_limits=[],evidence=[])]}
        packet=decision_bundle(accepted,matrix,quotes=False,planning=True)
        parsed=json.loads(packet[packet.index('{'):])
        self.assertEqual(parsed['reader_decisions'][0]['id'],'i01')
        self.assertEqual(parsed['reader_decisions'][0]['research_ids'],['q1'])
        self.assertEqual(parsed['decisions'][0]['id'],'q1')
        self.assertEqual(parsed['reader_design']['reader_needs'][0]['after_reading'],value['selected'][0]['after_reading'])
        self.assertEqual(len(accepted['items']),1)  # Emotion and examples did not add obligations.

    def test_prepared_product_cache_is_reused_only_for_the_exact_registered_inputs(self):
        job={'tenant_id':'owner','service_id':'service','category':'test','preset_id':None}
        service={'id':'service','tenant_id':'owner','name':'A','selling_points':['Webで相手を検索できる']}
        with patch.object(db,'get_job',return_value=job),patch.object(db,'get_service_by_id',return_value=service),\
             patch.object(db,'get_primary_sources',return_value=[]),patch.object(db,'get_company_settings',return_value=[]),\
             patch.object(db,'get_cached_product_context',return_value=None):
            brief,material,_,_=pb.prepare('j','keyword')
        product=pb.product_record({'value_connections':[],'maker_values':[],'unknowns':['未調査']},material)
        with patch.object(db,'get_job',return_value=job),patch.object(db,'get_service_by_id',return_value=service),\
             patch.object(db,'get_primary_sources',return_value=[]),patch.object(db,'get_company_settings',return_value=[]),\
             patch.object(db,'get_cached_product_context',return_value={'content_text':json.dumps(product)}) as cache:
            _,_,reused,status=pb.prepare('j','another keyword')
            self.assertEqual(status,'reused');self.assertEqual(reused['input_sha256'],brief['product_input_sha256'])
            self.assertEqual(cache.call_args.args[0],'owner')
            service['selling_points'].append('登録条件が変わった')
            _,_,reused,status=pb.prepare('j','another keyword')
            self.assertIsNone(reused);self.assertEqual(status,'unavailable')

    def test_resume_rejects_an_old_plan_even_when_question_ids_still_match(self):
        brief,_,sources,value=fixtures();plan=plan_fixture();plan['policy_sha256']=req.plan_policy()
        value['selected'][0]['after_reading']='変わった読後の判断'
        needs=rn.record(value,brief=brief,intent_sources=sources)
        with patch.object(req,'get_artifact',return_value={'content_text':json.dumps(plan)}),\
             patch.object(pb,'load',return_value=brief),patch.object(rn,'load',return_value=needs):
            with self.assertRaises(ContentQualityError):req.load_plan('j')

    def test_resume_rechecks_registered_material_once_per_pinned_run(self):
        from pipeline.article_revisions import RUN
        brief,material,sources,value=fixtures();plan=plan_fixture();plan['policy_sha256']=req.plan_policy()
        needs=rn.record(value,brief=brief,intent_sources=sources)
        token=RUN.set({'job_id':'j'})
        try:
            with patch.object(req,'get_artifact',return_value={'content_text':json.dumps(plan)}),\
                 patch.object(pb,'load',return_value=brief),patch.object(rn,'load',return_value=needs),\
                 patch.object(pb,'prepare',return_value=(brief,material,None,'miss')) as current:
                req.load_plan('j');req.load_plan('j');self.assertEqual(current.call_count,1)
        finally:RUN.reset(token)
        changed=copy.deepcopy(brief);changed['product_input_sha256']='changed'
        with patch.object(req,'get_artifact',return_value={'content_text':json.dumps(plan)}),\
             patch.object(pb,'load',return_value=brief),patch.object(rn,'load',return_value=needs),\
             patch.object(pb,'prepare',return_value=(changed,material,None,'miss')):
            with self.assertRaises(ContentQualityError):req.load_plan('j')

    def run_analysis(self, *, cached=False, stop='end_turn'):
        brief,material,sources,value=fixtures();records={}
        product=pb.product_record(value['product_understanding'],material) if cached else None
        if cached: del value['product_understanding']
        def save(**data):
            records[data['step']]={**data,'id':data['step']};return records[data['step']]
        with patch.object(intent,'prepare',return_value=(brief,material,product,'reused' if cached else 'miss')),\
             patch.object(intent,'get_artifact',return_value={'content_text':sources['serp']}),\
             patch.object(intent.anthropic,'Anthropic'),\
             patch.object(intent,'upsert_artifact',side_effect=save),\
             patch.object(intent,'create_with_retry',side_effect=[response({'searcher_stage':'情報収集'}),response(value,stop)]) as calls:
            if stop=='end_turn':intent.run('j',brief['keyword'])
            else:
                with self.assertRaises(ContentQualityError):intent.run('j',brief['keyword'])
        return records,calls

    def test_normal_intent_stage_has_two_calls_and_saves_the_linked_design(self):
        records,calls=self.run_analysis()
        self.assertEqual(calls.call_count,2)
        self.assertIn('reader_needs',records);self.assertIn('product_context',records)
        self.assertEqual(json.loads(records['intent_chains']['content_text'])['chains'],[])
        self.assertEqual(json.loads(records['reader_needs']['content_text'])['selected'][0]['id'],'i01')

    def test_cached_product_does_not_send_the_raw_material_again(self):
        _,calls=self.run_analysis(cached=True)
        payload=json.loads(calls.call_args.kwargs['messages'][0]['content'])
        self.assertIsNone(payload['product_materials']);self.assertIsNotNone(payload['product_context'])
        self.assertNotIn('product_understanding',calls.call_args.kwargs['output_config']['format']['schema']['properties'])

    def test_truncation_leaves_reader_design_unready_and_never_publishes_a_partial_graph(self):
        records,calls=self.run_analysis(stop='max_tokens')
        self.assertEqual(json.loads(records['reader_needs']['content_text'])['status'],'analyzing')
        self.assertNotIn('search_intent',records);self.assertEqual(calls.call_count,2)


if __name__=='__main__':unittest.main()


class ReaderReferenceContractTests(unittest.TestCase):
    def test_model_schema_exposes_only_real_reference_ids(self):
        schema=rn.output_schema(new_product=True)['format']['schema']
        refs=schema['properties']['selected']['items']['properties']['support']['items']['properties']['source']['enum']
        self.assertEqual(set(refs),{'','keyword','serp','request'})
        self.assertNotIn('SERP position 3 title',refs)

    def test_prompt_exposes_validation_requirements_for_hypotheses_and_examples(self):
        self.assertIn('query_hintsはモデルの推定',rn.POLICY)
        self.assertIn('kind=illustration',rn.POLICY)
        self.assertIn('kind=hypothesis',rn.POLICY)
        self.assertIn('support.field=context',rn.POLICY)
        self.assertIn('quoteも空文字',rn.POLICY)

    def test_unrecognized_reference_is_still_rejected(self):
        _,_,sources,value=fixtures()
        value['selected'][0]['support'][0]['source']='SERP position 3 title'
        with self.assertRaises(rn.ContentQualityError):rn.validate(value,sources,product_material_ids=['service:point:0'],has_product=True,has_cta=False)


class AnswerScopeHandoffTests(unittest.TestCase):
    def test_initial_analysis_is_preserved_as_provenance_not_rewritten(self):
        brief,_,_,value=fixtures()
        value['selected'][0]['answer_requirements']=['公式資料だけというAI由来の候補']
        original=copy.deepcopy(value)
        context=rn.editorial_context(brief,value)
        self.assertEqual(value,original)
        self.assertEqual(context['reader_needs'],value['selected'])
        self.assertEqual(context['answer_scope_policy'],rn.ANSWER_SCOPE_POLICY)
        self.assertIn('ユーザーの明示指定',context['answer_scope_policy'])

    def test_planning_review_and_downstream_share_scope_boundary(self):
        for system in (rn.POLICY,req.PLAN_SYSTEM,review.PLAN_REVIEW_SYSTEM):
            self.assertIn(rn.ANSWER_SCOPE_POLICY,system)
        context=rn.editorial_context(*[fixtures()[i] for i in (0,3)])
        self.assertIn('書き換え',context['answer_scope_policy'])
        self.assertIn('必要な回答を落とした場合',context['answer_scope_policy'])

    def test_authoring_and_repair_exclude_plan_rules_but_keep_original_request(self):
        from pipeline.generation_context import publication_design,bundle_data,repair_requirements
        brief,_,_,value=fixtures()
        brief['request']['custom_prompt']='記事目的に沿って推奨する'
        value['selected'][0]['tone']='中立的というAI由来の候補'
        context=rn.editorial_context(brief,value);original=copy.deepcopy(context)
        plan=plan_fixture();plan['reader_design']=context
        matrix={'valid':True,'policy_sha256':req.matrix_policy(),'items':[dict(id='q1',verified=True,status='confirmed',
            basis='guidance',answer='方法の違い',publication_conditions=[],claim_limits=[],evidence=[])]}
        variants=[publication_design(context),bundle_data(plan,matrix,quotes=False,planning=True)['reader_design'],
                  repair_requirements({'research_plan':plan})['reader_design']]
        for projected in variants:
            self.assertNotIn('answer_scope_policy',projected)
            self.assertEqual(projected['request'],brief['request'])
            self.assertEqual(projected['reader_needs'][0]['tone'],value['selected'][0]['tone'])
        self.assertEqual(context,original)
        self.assertEqual(context['answer_scope_policy'],rn.ANSWER_SCOPE_POLICY)

    def test_shared_scope_policy_change_invalidates_plan_acceptance(self):
        before=req.plan_policy()
        with patch.object(review,'PLAN_REVIEW_SYSTEM',review.PLAN_REVIEW_SYSTEM+'scope changed'):
            self.assertNotEqual(before,req.plan_policy())

    def test_explicit_user_source_limit_remains_in_the_handoff(self):
        brief,_,_,value=fixtures()
        brief['request']['custom_prompt']='この記事では公式情報だけを採用する'
        brief['request']['must_include']='各方法の制約を説明する'
        context=rn.editorial_context(brief,value)
        self.assertEqual(context['request'],brief['request'])
        context['request']['custom_prompt']='changed copy'
        self.assertEqual(brief['request']['custom_prompt'],'この記事では公式情報だけを採用する')
