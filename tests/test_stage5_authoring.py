from heading_review_fixture import with_heading_reviews
import copy
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import article_design as ad,authoring_policy as ap,content_quality as cq,focused_quality as fq
from pipeline import step_article,step_outline,step_review,step_content_audit,step_cta_inject
from pipeline import editorial_blueprint as bp,research_requirements as req
from pipeline.section_identity import bind_sections,valid_binding,carry_sections
from pipeline.quality_context import review_requirements
from test_reader_progression import chain
from test_editorial_blueprint import artifact,verdict
from reader_journey_fixture import article_journeys


def body(value):
    paragraphs=[
        '男性スタンダードは閲覧できます。送信はプレミアムのみです。閲覧と送信で必要な条件が異なるため、使いたい動作を先に整理しましょう。',
        '候補を見て検討したい場合と、相手へ連絡したい場合を分けて考えましょう。閲覧だけを使うか、送信まで使うかで、確認するプランが変わります。',
        '利用前に、選んだプランで自分が使いたい動作ができるかを確認しましょう。閲覧と送信の条件を分けて確認すると、用途に合う選択を考えられます。']
    return '# 用途に合う条件を選ぶ\n\n閲覧と送信の条件を整理します。\n\n'+'\n\n'.join('## '+s['title']+'\n\n'+p for s,p in zip(value['sections'],paragraphs))


def decision_result(design, text, missing=None):
    blocks=cq.content_blocks(text);target=next(b['id'] for b in blocks if '候補を見て' in b['text'])
    result={'decision_reviews':{d['id']:{'status':'fail' if missing is not None else 'pass',
        'reason':'本文は適用プランを示すが用途で選ぶ説明が不足' if missing is not None else '本文の「閲覧だけを使うか、送信まで使うか」で用途の選び分けを説明',
        'missing_requirements':[missing] if missing is not None else [],
        'affected_blocks':[{'id':target,'reason':'用途による選び分けを補う'}] if missing is not None else []} for d in design['reader_decisions']}}
    if 'lead_direction' in design:
        from pipeline.lead_review import KEYS
        result['lead_review']={key:{'status':'pass','reason':'合成したリード確認結果','affected_blocks':[]} for key in KEYS}
    journeys=article_journeys(design,text)
    result['reader_progression_reviews']={s['id']:{'status':'pass','reason':'この章の具体的な説明を確認する合成応答','affected_blocks':[],'journey':journeys[s['id']]}
        for s in design.get('sections',[])}
    return result


class Stage5AuthoringTests(unittest.TestCase):
    def test_shared_lead_policy_reaches_planning_writing_review_and_repair(self):
        for prompt in (step_outline.SYSTEM_PROMPT,step_article.SYSTEM_PROMPT,step_review.SYSTEM_PROMPT,cq.AUDIT_SYSTEM):
            self.assertEqual(prompt.count(ap.LEAD_POLICY),1)
            self.assertEqual(prompt.count(ap.PROGRESSION_WRITING_POLICY),1)
            self.assertEqual(prompt.count(ap.CTA_WRITING_POLICY),1)
            self.assertEqual(prompt.count(ap.SELECTION_POLICY),1)
            self.assertEqual(prompt.count(ap.SECTION_SCOPE_POLICY),1)
            self.assertEqual(prompt.count(ap.CLOSING_POLICY),1)
        # Positive fixed patterns are absent from the effective composed prompt.
        for old in ('構成は3パートで書く','共感→背景→解決の骨格は維持','concrete_phrase は原文のまま活かす','▼ H2末尾の感情補完文'):
            self.assertNotIn(old,step_article.SYSTEM_PROMPT)

    def test_actual_writer_call_uses_adaptive_lead_and_preserves_materials(self):
        for question in ('AとBをどう選ぶか','設定を変更する手順','会話が続かず不安'):
            msg=NS(stop_reason='end_turn',content=[NS(type='text',text='# 記事\n\n説明。')],usage=NS(input_tokens=1,output_tokens=1))
            with patch.object(step_article,'create_with_retry',return_value=msg) as send:
                step_article._call(None,[{'role':'user','content':question}])
            request=send.call_args.kwargs
            self.assertIn(question,request['messages'][0]['content'])
            self.assertIn(ap.LEAD_POLICY,request['system'])
            self.assertEqual(request['system'].count(ap.LEAD_POLICY),1)

    def test_company_priority_is_shared_without_asserting_rank(self):
        companies=[{'name':'B','recommend_level':2},{'name':'A','recommend_level':5},{'name':'C','recommend_level':0}]
        a=step_article._build_company_prompt(companies);b=step_outline._build_company_prompt(companies)
        self.assertEqual(a,b);self.assertLess(a.index('"A"'),a.index('"B"'));self.assertNotIn('"C"',a)
        self.assertNotIn('最強おすすめ',a);self.assertNotIn('比較表1位・強い推薦文',a)
        self.assertIn('優先度は扱う重点',a)
        self.assertIn('リスト外は紹介・言及しない',step_article._build_company_prompt(companies,'registered_only'))

    def test_registered_claims_are_not_reintroduced_as_service_truth(self):
        service={'name':'A','url':'https://a.example','selling_points':['UNVERIFIED_SUCCESS_99'],'raw_content':'UNVERIFIED_PRICE_100','must_include':'利用条件を説明','must_exclude':'断定しない'}
        for prompt in (step_article._build_service_prompt(service),step_outline._build_service_prompt(service)):
            self.assertNotIn('UNVERIFIED_',prompt);self.assertIn('利用条件を説明',prompt)
            self.assertIn('今回の読者設計',prompt);self.assertNotIn('主要な紹介対象として',prompt)

    def test_registered_cta_promise_and_targets_are_available_without_duplication(self):
        cta={'name':'相談','body':'利用条件について相談できます','button_text':'相談する','url':'https://a.example/consult'}
        text=step_article._build_cta_prompt(cta,{'cta_after_h2':['用途を選ぶ']})
        for val in cta.values():self.assertIn(val,text)
        self.assertIn('用途を選ぶ',text);self.assertIn('事実の根拠ではない',text)
        self.assertIn('登録CTAの完成文面を構成へ複製しない',step_outline._build_cta_prompt(cta))
        self.assertIn('重複生成しない',step_article.SYSTEM_PROMPT)

    def test_secondary_cta_bridge_reaches_its_part_without_primary_fallback(self):
        sm={'blueprint_version':bp.VERSION,'primary_h2':'最初の章','per_section_instructions':{'最初の章':'第一の案内','二つ目の章':'第二の案内'},'cta_after_h2':['最初の章','二つ目の章']}
        instructions=step_article._build_part_instructions(None,[('最初の章',5,600),('二つ目の章',4,600),('確認する章',3,600)],sm,'A')
        self.assertIn('第一の案内',instructions[0]);self.assertIn('第二の案内',instructions[1]);self.assertNotIn('重点的に紹介', ''.join(instructions))
        self.assertNotIn('セクションとまとめまで',instructions[2]);self.assertIn('構成にないまとめを追加しない',instructions[2])

    def test_word_count_review_cannot_remove_or_merge_headings(self):
        action=step_review._word_count_action(9000,'5000字')
        self.assertIn('見出しの削除・統合・移動は行わず',action)
        self.assertNotIn('補助H2を削除できます',action)
        self.assertNotIn('最優先',step_review.WORD_COUNT_INSTRUCTION)

    def test_blueprint_ids_map_to_body_sections_without_public_id_markers(self):
        _,_,_,ctx,value=chain();a=artifact(value,ctx);text=body(value);design=ad.design_context(a)
        binding=bind_sections(text,a['content_text'])
        self.assertEqual([e['blueprint_id'] for e in binding['entries']],['s01','s02','s03'])
        self.assertTrue(valid_binding(text,a['content_text'],binding))
        mapped=ad.correspondence(text,a['content_text'],design,binding)
        self.assertTrue(all(s['block_ids'] and s['status']=='mapped_not_semantically_verified' for s in mapped['sections']))
        self.assertNotIn('s01',text)

    def test_missing_body_section_is_a_location_gap_not_semantic_success(self):
        _,_,_,ctx,value=chain();a=artifact(value,ctx);text=body(value);text=text[:text.index('## '+value['sections'][2]['title'])]
        result=ad.correspondence(text,a['content_text'],ad.design_context(a))
        self.assertEqual(result['sections'][2]['status'],'unresolved_location')
        self.assertFalse(result['sections'][2]['block_ids'])

    def test_carried_heading_changes_keep_exact_blueprint_identity(self):
        _,_,_,ctx,value=chain();a=artifact(value,ctx);text=body(value);binding=bind_sections(text,a['content_text'])
        changed=text.replace(value['sections'][1]['title'],'用途を確かめる')
        carried=carry_sections(text,changed,a['content_text'],binding)
        mapped=ad.correspondence(changed,a['content_text'],ad.design_context(a),carried)
        self.assertEqual(mapped['sections'][1]['article_section_ids'],['section-001'])
        carried['entries'][1]['blueprint_id']='s99'
        self.assertFalse(valid_binding(changed,a['content_text'],carried))

    def test_design_is_loaded_into_final_requirements_and_only_coverage_role(self):
        _,_,_,ctx,value=chain();a=artifact(value,ctx)
        with patch.object(cq,'intent_review_requirements',return_value={}),patch.object(cq,'astra_review_enabled',return_value=False),patch('pipeline.db.get_optional_artifact',return_value=a):
            requirements=cq.final_review_requirements({'id':'job'},'条件')
        self.assertEqual(requirements['article_design']['blueprint_sha256'],bp.fingerprint(value))
        for role in fq.ROLES:
            self.assertEqual('article_design' in review_requirements(requirements,role),role=='coverage')

    def test_legacy_requirements_do_not_invent_reader_design(self):
        with patch.object(cq,'intent_review_requirements',return_value={}),patch.object(cq,'astra_review_enabled',return_value=False),patch('pipeline.db.get_optional_artifact',return_value={'content_text':'旧構成'}):
            self.assertNotIn('article_design',cq.final_review_requirements({'id':'job'},'条件'))

    def test_design_context_never_copies_source_quotes_or_registration_notes(self):
        _,_,_,ctx,value=chain();result=ad.design_context(artifact(value,ctx));text=json.dumps(result,ensure_ascii=False)
        self.assertNotIn('blueprint_context',text);self.assertNotIn('sources',result);self.assertNotIn('quote',text)
        self.assertIn('reader_progression',text);self.assertIn('q1.c02',text)

    def test_missing_reader_answer_overrides_general_coverage_pass(self):
        _,_,_,ctx,value=chain();text=body(value);design=ad.design_context(artifact(value,ctx));r=decision_result(design,text,1)
        checks=[{'key':'coverage','status':'pass','reason':'見出しは全件あり','affected_blocks':[]}]
        checked=ad.checked_result(checks,r,design,text)
        self.assertEqual(checked[0]['status'],'fail');self.assertTrue(checked[0]['affected_blocks']);self.assertEqual(checks[0]['status'],'pass')

    def test_individual_reviews_cannot_omit_required_elements_or_fake_block_ids(self):
        _,_,_,ctx,value=chain();text=body(value);design=ad.design_context(artifact(value,ctx))
        for change in ('missing','block','contradiction'):
            r=decision_result(design,text,1)
            if change=='missing':r={}
            elif change=='block':r['decision_reviews']['i01']['affected_blocks'][0]['id']='not-a-block'
            else:r['decision_reviews']['i01']['missing_requirements']=[]
            with self.assertRaises(cq.ContentQualityError):ad.checked_result([{'key':'coverage','status':'pass','reason':'仮'}],r,design,text)

    def test_supporting_answer_can_be_omitted_without_hiding_primary_failure(self):
        _,_,_,ctx,value=chain();design=ad.design_context(artifact(value,ctx));text=body(value)
        support=copy.deepcopy(design['reader_decisions'][0]);support.update(id='i02',role='supporting');design['reader_decisions'].append(support)
        r=decision_result(design,text)
        r['decision_reviews']['i02'].update(status='fail',missing_requirements=[0],reason='主要判断には不要な補足',affected_blocks=[])
        self.assertEqual(ad.checked_result([{'key':'coverage','status':'pass','reason':'主要回答あり'}],r,design,text)[0]['status'],'pass')

    def audit_fixture(self,provider,routing,missing=None):
        _,pages,_,ctx,value=chain();a=artifact(value,ctx);text=body(value);design=ad.design_context(a);requests=[]
        def send(job,step,request):
            requests.append(request)
            if step.startswith('tiered_article_reader_'):
                ids=request['output_config']['format']['schema']['properties']['reader_journeys']['properties']
                rows=decision_result(design,text,missing)['reader_progression_reviews']
                return {'reader_journeys':{sid:rows[sid]['journey'] for sid in ids}},NS(input_tokens=1,output_tokens=1)
            role='evidence' if step=='tiered_article_evidence_scope' else step.removeprefix('tiered_article_');keys=fq.ROLES[role]
            result={'checks':[{'key':k,'status':'pass','reason':'合成確認','affected_blocks':[],**({'requires_source_check':False,'research_ids':[],'source_urls':[]} if role in ('evidence','coverage') and routing=='focused' else {})} for k in keys]}
            if role != 'evidence':result['editorial_findings']=[]
            if role=='coverage':result.update(decision_result(design,text,missing))
            return with_heading_reviews(result, request),NS(input_tokens=1,output_tokens=1)
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':provider,'QUALITY_RESEARCH_ROUTING':routing}),patch('pipeline.tiered_research.checked_request',side_effect=send):
            # Legacy evidence routing keeps this test on the five normal roles.
            # Focused's original evidence screen is replaced by its existing
            # checked-request path below using successful unchanged evidence.
            report=fq.audit_article(None,text=text,facts='',outline=a['content_text'],contract={'required_sections':[]},requirements={'article_design':design},sources=cq.source_evidence({'content_text':json.dumps(pages)}))
        return report,requests,dict(text=text,facts='',outline=a['content_text'],contract={'required_sections':[]},requirements={'article_design':design},sources=cq.source_evidence({'content_text':json.dumps(pages)}))

    def test_existing_coverage_call_checks_actual_answers_without_new_role(self):
        report,requests,_=self.audit_fixture('tiered','legacy',1)
        self.assertEqual(len(requests),6);self.assertFalse(report['valid'])
        self.assertEqual(report['phases']['coverage']['decision_reviews']['i01']['missing_requirements'],[1])
        coverage=[r for r in requests if 'decision_reviews' in r['output_config']['format']['schema']['properties']]
        self.assertEqual(len(coverage),1)
        self.assertIn('reader_correspondence',json.loads(coverage[0]['messages'][0]['content']))

    def test_final_acceptance_requires_individual_body_reviews(self):
        report,_,context=self.audit_fixture('tiered','legacy')
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'}):
            expected=cq.snapshot(**context);cq._require_audit_strict(report,expected,stage='article',context=context)
            broken=copy.deepcopy(report);broken['phases']['coverage'].pop('decision_reviews')
            with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(broken,expected,stage='article',context=context)

    def test_changed_body_or_design_cannot_reuse_correspondence_or_acceptance(self):
        report,_,context=self.audit_fixture('tiered','legacy')
        changed=copy.deepcopy(context);changed['requirements']['article_design']['reader_decisions'][0]['answer_requirements'].append('新しい必須回答')
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'}):
            with self.assertRaises(cq.ContentQualityError):cq._require_audit_strict(report,cq.snapshot(**changed),stage='article',context=changed)
        old=report['phases']['coverage']['reader_correspondence']
        new=ad.correspondence(context['text']+'\n補足',context['outline'],context['requirements']['article_design'])
        self.assertNotEqual(old['text_sha256'],new['text_sha256'])

    def test_coverage_repair_keeps_reader_design_in_compact_packet(self):
        _,_,_,ctx,value=chain();design=ad.design_context(artifact(value,ctx));text=body(value)
        packet={'requirements':{},'article_blocks':cq.content_blocks(text),'target_block_ids':['block-0004']}
        report={'checks':[{'key':'coverage','status':'fail','reason':'必要回答不足'}]}
        with patch('pipeline.quality_context.compact_enabled',return_value=True),patch('pipeline.review_scope.repair_packet',return_value=packet),patch('pipeline.step_content_audit.hybrid_writing_enabled',return_value=False):
            request,_=step_content_audit.prepare_content_repair(None,job_id='job',text=text,report=report,requirements={'article_design':design},sources='[]',facts='',contract={},issues=[])
        payload=json.loads(request['messages'][0]['content'])
        self.assertEqual(payload['requirements']['article_design'],design)
        self.assertIn(ap.LEAD_POLICY,request['system'])

    def test_real_cta_injection_preserves_promise_and_does_not_duplicate_bridge(self):
        _,_,_,ctx,value=chain();ctx['reader_design']['product_fit']['cta_role']='appropriate';value['sections'][1]['cta']='用途が合い相談したい場合に、利用条件の相談へ案内する'
        a=artifact(value,ctx);text=body(value);article={'content_text':text,'meta':{'section_map':bind_sections(text,a['content_text'])}}
        sm={'content_text':json.dumps(bp.placement(a),ensure_ascii=False)}
        cta={'name':'相談','body':'利用条件について相談できます','button_text':'相談する','url':'https://a.example/consult'}
        with patch.object(step_cta_inject,'get_artifact',side_effect=lambda j,k:article if k=='article' else a if k=='outline' else sm),patch.object(step_cta_inject,'get_job',return_value={'cta_id':'c'}),patch.object(step_cta_inject,'get_cta_by_id',return_value=cta),patch('pipeline.db.get_optional_artifact',return_value=a),patch.object(step_cta_inject,'upsert_artifact',side_effect=lambda **kw:{'id':'saved',**kw}):
            result=step_cta_inject.run('job','条件')
        self.assertEqual(result['content_text'].count('[相談する](https://a.example/consult)'),1)
        self.assertIn('> 利用条件について相談できます',result['content_text'])
        self.assertEqual(result['meta']['cta_placement']['targets'],[value['sections'][1]['title']])

    def test_focused_routing_uses_same_five_roles_and_retains_reader_verdicts(self):
        report,requests,_=self.audit_fixture('tiered','focused',1)
        self.assertEqual(len(requests),6);self.assertFalse(report['valid'])
        self.assertEqual(report['phases']['coverage']['decision_reviews']['i01']['missing_requirements'],[1])
        for request in requests:
            data=json.loads(request['messages'][0]['content'])
            if 'requirements' in data and 'decision_reviews' not in request['output_config']['format']['schema']['properties']:
                self.assertNotIn('article_design',data['requirements'])

    def test_complete_writer_saves_draft_and_exact_reader_mapping_with_cta_contract(self):
        plan,pages,matrix,ctx,value=chain();ctx['reader_design']['product_fit']['cta_role']='appropriate';plan['reader_design']['product_fit']['cta_role']='appropriate'
        value['sections'][0]['reader_progression']['before']['emotion']='機能の違いを見落としそうで心配という仮説'
        value['sections'][2]['reader_progression']['before']['emotion']='利用前の確認を忘れそうで気がかりという仮説'
        value['sections'][1]['cta']='用途が合い相談したい場合に、利用条件の相談へ案内する'
        a=artifact(value,ctx);full=body(value);parts=full.split('\n\n## ')
        chunks=[parts[0]+'\n\n## '+parts[1],'## '+parts[2],'## '+parts[3]]
        contract={'required_sections':[]};facts=req.accepted_facts(matrix,plan);rctx=bp.review_context(a)
        base_req={'keyword':'条件','research_plan':plan,'research_decisions':matrix}
        readiness={**verdict(rctx),'blueprint_review_context':rctx,'valid':True,'stage':'research','policy_version':cq.POLICY_VERSION,
            'checks':[{'key':k,'status':'pass','reason':'合成確認'} for k in cq.CHECKS],
            'snapshot':cq.snapshot(a['content_text'],cq.confirmed_facts(facts),a['content_text'],contract,{**base_req,'editorial_design':rctx},cq.source_evidence({'content_text':json.dumps(pages)}))}
        cta={'name':'相談','body':'利用条件について相談できます','button_text':'相談する','url':'https://a.example/consult'}
        artifacts={'outline':a,'fact_sheet':{'content_text':facts},'fresh_sources':{'content_text':json.dumps(pages)},
            'content_contract':{'content_text':json.dumps(contract)},'search_intent':{'content_text':'old','meta':{'reader_needs_version':'new'}},
            'research_validation':{'content_text':json.dumps(readiness)},'service_map':{'content_text':json.dumps(bp.placement(a))}}
        def save(**kw):artifacts[kw['step']]={'id':'saved',**kw};return artifacts[kw['step']]
        index=iter([*chunks,json.dumps({'lead':'閲覧と送信の条件を整理します。'},ensure_ascii=False)])
        def send(*args,**kw):return NS(stop_reason='end_turn',content=[NS(type='text',text=next(index))],usage=NS(input_tokens=1,output_tokens=1))
        with ExitStack() as stack:
            stack.enter_context(patch.object(step_article,'get_artifact',side_effect=lambda j,k:artifacts[k]))
            stack.enter_context(patch.object(step_article,'get_job',return_value={'id':'job','cta_id':'cta'}))
            stack.enter_context(patch.object(step_article,'get_cta_by_id',return_value=cta))
            stack.enter_context(patch.object(step_article,'upsert_artifact',side_effect=save))
            stack.enter_context(patch.object(step_article,'intent_review_requirements',return_value=copy.deepcopy(base_req)))
            stack.enter_context(patch('pipeline.research_requirements.require_authoring_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.generation_context.authoring_intent',return_value='reader design'))
            stack.enter_context(patch.object(step_article.anthropic,'Anthropic'))
            stack.enter_context(patch.object(step_article.time,'sleep'))
            model=stack.enter_context(patch.object(step_article,'create_with_retry',side_effect=send))
            result=step_article.run('job','条件')
        self.assertEqual(model.call_count,4)
        # Actual normal writer requests, with no operator custom prompt: each
        # part gets its own states without importing a different chapter's fear.
        from pipeline.article_design import part_reader_brief
        split=step_article._split_sections_into_parts([(s['title'],s['budget']['importance'],s['budget']['chars']) for s in value['sections']])
        for call,sections in zip(model.call_args_list[:3],split):
            instruction=next(m['content'] for m in reversed(call.kwargs['messages']) if m['role']=='user')
            brief=part_reader_brief(value,[s[0] for s in sections],a['meta']['blueprint_context'])
            self.assertTrue(brief)
            self.assertIn(brief,instruction)
            local=instruction.split('【今回のPARTで実現する読者への回答】')[-1]
            for section in value['sections']:
                feeling=section['reader_progression']['before']['emotion']
                if feeling and section['title'] not in [s[0] for s in sections]:self.assertNotIn(feeling,local)
        self.assertIn('article_draft',artifacts)
        self.assertIn('article_lead_edit',artifacts)
        lead_edit=json.loads(artifacts['article_lead_edit']['content_text'])
        self.assertTrue(lead_edit['body_preserved']);self.assertFalse(lead_edit['semantically_accepted'])
        self.assertEqual([e['blueprint_id'] for e in result['meta']['section_map']['entries']],['s01','s02','s03'])
        self.assertTrue(all(s['status']=='mapped_not_semantically_verified' for s in result['meta']['reader_correspondence']['sections']))
        actual=json.dumps(model.call_args_list[0].kwargs,ensure_ascii=False)
        self.assertIn('利用条件について相談できます',actual);self.assertIn(ap.LEAD_POLICY,model.call_args_list[0].kwargs['system'])
        self.assertNotIn('[相談する]',result['content_text'])
        self.assertNotIn('セクションID',result['content_text'])

    def test_non_tiered_audit_also_requests_individual_reader_answers(self):
        _,pages,_,ctx,value=chain();a=artifact(value,ctx);text=body(value);design=ad.design_context(a);requests=[]
        roles=iter(fq.ROLES)
        def send(*args,**request):
            requests.append(request)
            if 'reader_journeys' in request['output_config']['format']['schema']['properties']:
                result={'reader_journeys':{sid:row['journey'] for sid,row in decision_result(design,text)['reader_progression_reviews'].items()}}
                return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(result,ensure_ascii=False))],usage=NS(input_tokens=1,output_tokens=1))
            role=next(roles)
            result={'checks':[{'key':k,'status':'pass','reason':'合成確認','affected_blocks':[]} for k in fq.ROLES[role]]}
            if role != 'evidence':result['editorial_findings']=[]
            if role=='coverage':result.update(decision_result(design,text,1))
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(with_heading_reviews(result, request),ensure_ascii=False))],usage=NS(input_tokens=1,output_tokens=1))
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','QUALITY_RESEARCH_ROUTING':'legacy'}),patch.object(cq,'create_with_retry',side_effect=send):
            report=fq.audit_article(None,text=text,facts='',outline=a['content_text'],contract={'required_sections':[]},requirements={'article_design':design},sources=cq.source_evidence({'content_text':json.dumps(pages)}))
        self.assertEqual(len(requests),6);self.assertFalse(report['valid'])
        self.assertIn('decision_reviews',report['phases']['coverage'])
