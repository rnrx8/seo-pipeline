import copy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import claim_evidence as ce, research_requirements as req
from pipeline.content_quality import ContentQualityError
from pipeline.generation_context import decision_bundle,authoring_intent,registered_for_authoring,repair_requirements
from pipeline.source_spans import indexed_sources,expand_references,span_schema
from pipeline.tiered_research import validate_visible_matrix,needs_adjudication,record_source_review


def fixture():
    pages=[dict(url='https://a.example/pricing',status='success',text='男性スタンダードは閲覧できます。30日契約は総額3,000円です。',fetched_at='2026-10-10T00:00:00Z'),
           dict(url='https://a.example/faq',status='success',text='送信はプレミアムのみです。',fetched_at='2026-10-11T00:00:00Z')]
    plan={'items':[dict(id='q1',question='Aで閲覧・送信する条件',subject='A',priority='essential',required=True,
        priority_reason='利用判断',source_requirement='standard',official_check_required=True,requires_current=False)]}
    refs=[dict(url=p['url'],quote=p['text'],source_kind='primary',independence_group=p['url']) for p in pages]
    claim=dict(text='男性スタンダードは閲覧できます。',subject='A',operation='閲覧',answer_type='sourced',basis='primary',
        supports_current_conclusion=True,conditions=[dict(dimension='audience',value='男性'),dict(dimension='plan',value='スタンダード')],
        evidence_indices=[0],required_for_answer=True,disposition='publish',conflict=dict(status='none',evidence_indices=[],resolution=''))
    value={'coverage_sufficient':True,'coverage_reason':'必要条件を照合','items':[dict(id='q1',reason='公式の適用範囲を確認',
        evidence=refs,official_checked_urls=[p['url'] for p in pages],applicable_at='掲載条件',omission_reason='',
        claim_limits=[],exploration_complete=True,exploration_reason='料金とFAQを照合',claims=[claim])]}
    return plan,pages,value


class ClaimEvidenceTests(unittest.TestCase):
    def test_reviewed_source_survives_empty_claims_for_optional_omission_only(self):
        for quoted in (True,False):
            plan,pages,value=fixture();question=plan['items'][0]
            question.update(required=False,priority='supporting',source_requirement='expert_allowed',official_check_required=False)
            item=value['items'][0]
            item.update(claims=[],official_checked_urls=[],omission_reason='本文では補足を使わない')
            if not quoted:item['evidence'][0]['quote']='提示原文にはない引用'
            req.validate_matrix(value,plan,pages)
            self.assertEqual(item['evidence'],[])
            self.assertTrue(item['claim_evidence'])
            record_source_review(value['items'],pages[:1])
            # The second URL was not visible and must not be recorded.
            self.assertEqual(item.get('reviewed_source_urls',[]),[pages[0]['url']] if quoted else [])
            req.propose_optional_omissions(value['items'],plan,pages)
            gaps=req.validate_matrix(value,plan,pages)
            self.assertEqual(bool(gaps),not quoted)
            self.assertEqual(req.accepted_facts(value,plan),'')
            if quoted:
                value.update(coverage_sufficient=False,coverage_reason='読者判断に必要',coverage_issues=[{'id':'q1','reason':'省略不可'}])
                self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_answer_is_derived_from_individual_claims_and_scoped_evidence(self):
        plan,pages,value=fixture();value['items'][0]['answer']='古い送信可能という合成文'
        value['items'][0]['claims'][0]['id']='untrusted-old-id'
        self.assertFalse(req.validate_matrix(value,plan,pages));item=value['items'][0]
        self.assertEqual(item['answer'],'男性スタンダードは閲覧できます。')
        self.assertEqual(item['claim_states'][0]['id'],'q1.c01')
        self.assertEqual(item['claim_states'][0]['operation'],'閲覧')
        self.assertEqual(len(item['evidence']),1)

    def test_output_schema_has_one_source_of_truth_for_answer_and_conditions(self):
        props=req.MATRIX_SCHEMA['format']['schema']['properties']['items']['items']['properties']
        for key in ('answer','publication_conditions','status','basis'):self.assertNotIn(key,props)
        self.assertIn('conditions',props['claims']['items']['required'])
        for key in ('text','subject','operation'):
            self.assertEqual(props['claims']['items']['properties'][key]['minLength'],1)

    def test_two_operations_keep_their_own_plan_and_source_mapping(self):
        plan,pages,value=fixture();second=copy.deepcopy(value['items'][0]['claims'][0])
        second.update(text='送信はプレミアムのみです。',operation='送信',conditions=[dict(dimension='plan',value='プレミアム')],evidence_indices=[1])
        value['items'][0]['claims'].append(second)
        self.assertFalse(req.validate_matrix(value,plan,pages))
        value.update(valid=True,policy_sha256=req.matrix_policy())
        packet=decision_bundle(plan,value,quotes=True);data=json.loads(packet[packet.index('{'):])
        claims=data['decisions'][0]['claims']
        self.assertEqual(claims[0]['conditions'][1]['value'],'スタンダード')
        self.assertEqual(claims[1]['conditions'][0]['value'],'プレミアム')
        self.assertEqual(data['sources'][claims[1]['source_ids'][0]]['url'],pages[1]['url'])
        self.assertNotIn('claim_evidence',data['decisions'][0])

    def test_contract_term_and_price_basis_are_not_silently_converted(self):
        plan,pages,value=fixture();claim=value['items'][0]['claims'][0]
        claim.update(text='30日契約は総額3,000円です。',operation='契約',conditions=[
            dict(dimension='term',value='30日契約'),dict(dimension='price_basis',value='一括総額3,000円')])
        self.assertFalse(req.validate_matrix(value,plan,pages))
        facts=req.accepted_facts(value,plan)
        self.assertIn('30日契約',facts);self.assertIn('一括総額3,000円',facts)
        self.assertNotIn('1ヶ月契約',facts)

    def test_sample_and_target_period_stay_with_the_statistic(self):
        plan,pages,value=fixture();pages[0]['text']='2025年の回答者100人中50人の自己申告です。'
        item=value['items'][0];item['evidence'][0]['quote']=pages[0]['text']
        item['claims'][0].update(text=pages[0]['text'],conditions=[dict(dimension='time',value='2025年'),
            dict(dimension='audience',value='回答者100人の自己申告')],supports_current_conclusion=False)
        self.assertFalse(req.validate_matrix(value,plan,pages))
        ledger=value['evidence_ledger']['claims'][0]
        self.assertEqual(ledger['evidence'][0]['fetched_at'],'2026-10-10T00:00:00Z')
        self.assertIn('2025年',json.dumps(ledger['conditions'],ensure_ascii=False))
        self.assertIsNotNone(ledger['checked_at'])

    def test_unresolved_critical_conflict_is_not_published_with_a_qualifier(self):
        plan,pages,value=fixture();claim=value['items'][0]['claims'][0]
        claim.update(disposition='qualified',conflict=dict(status='unresolved',evidence_indices=[1],resolution='対象条件が一致せず未解決'))
        gaps=req.validate_matrix(value,plan,pages)
        self.assertTrue(gaps);self.assertIn('q1.c01',gaps[0]['reason'])
        self.assertFalse(value['evidence_ledger']['claims'][0]['publishable'])

    def test_resolved_conflict_needs_an_explanation_and_real_original_text(self):
        plan,pages,value=fixture();claim=value['items'][0]['claims'][0]
        claim['conflict']=dict(status='resolved',evidence_indices=[1],resolution='FAQは別動作の送信、価格表は閲覧を説明している')
        self.assertFalse(req.validate_matrix(value,plan,pages))
        value['items'][0]['claim_evidence'][1]['quote']='捏造したFAQ'
        self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_missing_optional_identifier_does_not_erase_a_verified_main_claim(self):
        plan,pages,value=fixture();unknown=copy.deepcopy(value['items'][0]['claims'][0])
        unknown.update(text='補助的な番号',operation='番号',required_for_answer=False,disposition='withhold',
                       evidence_indices=[],conflict=dict(status='unresolved',evidence_indices=[1],resolution='補助的な番号は未解決'))
        value['items'][0]['claims'].append(unknown)
        self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertIn('閲覧できます',value['items'][0]['answer']);self.assertNotIn('補助的な番号',value['items'][0]['answer'])

    def test_invalid_indices_cannot_use_python_negative_indexing_or_boolean_indices(self):
        for indices,problems in (([-1],['out_of_range']),([True],['non_integer']),([999],['out_of_range']),
                                 ([0,0],['duplicate']),(None,['not_list']),(['0'],['non_integer'])):
            plan,pages,value=fixture();value['items'][0]['claims'][0]['evidence_indices']=indices
            gaps=req.validate_matrix(value,plan,pages);item=value['items'][0];row=item['claim_states'][0]
            self.assertFalse(row['publishable']);self.assertEqual(row['evidence'],[])
            # The rejected response is kept verbatim, never clamped or filtered.
            self.assertEqual(row['evidence_indices'],indices)
            self.assertEqual(row['reference_issue']['fields'],[{'field':'evidence_indices','indices':indices,'problems':problems}])
            self.assertFalse(item['verified']);self.assertEqual(item['answer'],'');self.assertEqual(item['evidence'],[])
            self.assertEqual(gaps[0]['validation_issue']['code'],ce.REFERENCE_ISSUE);self.assertIn('q1.c01',gaps[0]['reason'])
            self.assertEqual(req.accepted_facts(value,plan),'')

    def test_first_and_last_valid_indices_are_accepted(self):
        plan,pages,value=fixture();value['items'][0]['claims'][0]['evidence_indices']=[1,0]
        self.assertFalse(req.validate_matrix(value,plan,pages))
        item=value['items'][0]
        self.assertTrue(item['verified']);self.assertEqual(len(item['claim_states'][0]['evidence']),2)
        self.assertNotIn('reference_issue',item['claim_states'][0])

    def test_invalid_conflict_reference_is_rejected_without_hiding_the_original_conflict(self):
        plan,pages,value=fixture();claim=value['items'][0]['claims'][0]
        claim['conflict']=dict(status='resolved',evidence_indices=[2],resolution='別の動作を説明')
        gaps=req.validate_matrix(value,plan,pages);row=value['items'][0]['claim_states'][0]
        self.assertEqual(row['reference_issue']['fields'][0]['field'],'conflict.evidence_indices')
        self.assertEqual(row['conflict']['evidence_indices'],[2]);self.assertEqual(row['conflicting_evidence'],[])
        self.assertFalse(value['items'][0]['verified']);self.assertTrue(gaps)

    def test_optional_claim_with_invalid_reference_still_requires_rereview(self):
        plan,pages,value=fixture();extra=copy.deepcopy(value['items'][0]['claims'][0])
        extra.update(text='補足',operation='補足',required_for_answer=False,evidence_indices=[5])
        value['items'][0]['claims'].append(extra)
        gaps=req.validate_matrix(value,plan,pages);item=value['items'][0]
        self.assertTrue(item['claim_states'][0]['publishable']);self.assertFalse(item['verified'])
        self.assertIn('q1.c02',gaps[0]['reason'])

    def test_reference_defect_is_not_offered_as_an_omission_before_rereview(self):
        for defer in (True,False):
            plan,pages,value=fixture();plan['items'][0].update(required=False,priority='supporting')
            item=value['items'][0];item['claims'][0]['evidence_indices']=[5];item['exploration_reason']='公式を確認'
            req.validate_matrix(value,plan,pages)
            req.propose_optional_omissions(value['items'],plan,pages,defer_reference_issues=defer)
            self.assertEqual(item.get('basis')=='omitted',not defer)

    def test_full_source_revalidation_cannot_promote_an_unseen_quote(self):
        plan,pages,value=fixture();short=copy.deepcopy(pages);short[0]['text']='本文は抜粋です。'
        validate_visible_matrix(value,plan,short)
        self.assertTrue(req.validate_matrix(value,plan,pages))
        self.assertFalse(value['items'][0]['verified'])
        _,_,new=fixture();self.assertFalse(req.validate_matrix(new,plan,pages))

    def test_focused_review_pending_flag_is_not_overridden_by_claim_projection(self):
        plan,pages,value=fixture();value['items'][0]['additional_sources_needed']=True
        self.assertTrue(req.validate_matrix(value,plan,pages))
        self.assertFalse(value['items'][0]['verified'])

    def test_complete_tiered_routes_keep_claims_through_screen_adjudication_and_coverage(self):
        from pipeline import tiered_research as tr
        for routing in ('legacy','focused','focused-overcautious'):
            plan,pages,template=fixture();calls=[]
            def ask(job,step,request,**kwargs):
                calls.append(step)
                payload=json.loads(request['messages'][0]['content'])
                if step=='tiered_coverage':
                    self.assertTrue(payload['decisions'][0]['claims'])
                    return {'coverage_sufficient':True,'coverage_reason':'主張と条件を照合','coverage_issues':[]},SimpleNamespace(input_tokens=1,output_tokens=1)
                row=copy.deepcopy(template['items'][0]);row.pop('id')
                if routing=='focused-overcautious' and step.startswith('tiered_screen'):
                    row['claims'][0]['disposition']='withhold'
                row['evidence']=[]
                for page in payload['sources']:
                    row['evidence'].append(dict(source_ref=page['excerpts'][0]['source_ref'],source_kind='primary',
                        independence_group=page['url'],expert_source_ref='',expert_name='',expert_scope_reason=''))
                schema_row=request['output_config']['format']['schema']['properties']['items']['properties']['q1']
                if 'additional_sources_needed' in schema_row['properties']:
                    row['additional_sources_needed']=routing=='focused-overcautious' and step.endswith('_reread')
                return {'items':{'q1':row},'coverage_sufficient':True,'coverage_reason':'原文を照合'},SimpleNamespace(input_tokens=1,output_tokens=1)
            with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':'focused' if routing.startswith('focused') else routing}),\
                 patch.object(tr,'get_optional_artifact',return_value=None),patch.object(tr,'checked_request',side_effect=ask):
                value,_=tr.audit_matrix('j',plan,pages,'draft',{})
            self.assertFalse(req.validate_matrix(value,plan,pages))
            value.update(valid=True,policy_sha256=req.matrix_policy())
            packet=decision_bundle(plan,value,quotes=True)
            data=json.loads(packet[packet.index('{'):]);self.assertEqual(data['decisions'][0]['claims'][0]['id'],'q1.c01')
            self.assertIn('tiered_coverage',calls);self.assertGreaterEqual(len(calls),3)
            if routing=='focused-overcautious':self.assertTrue(any(c.endswith('_audit') for c in calls))

    def test_general_editorial_proposal_does_not_require_a_matching_published_sentence(self):
        plan,pages,value=fixture();q=plan['items'][0];q.update(source_requirement='general_guidance',official_check_required=False)
        item=value['items'][0];item['claims'][0].update(answer_type='proposal',basis='guidance',evidence_indices=[],
            text='最初に希望条件を二つ書いてみましょう。',conditions=[],operation='提案')
        self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertIn('[editorial_proposal]',req.accepted_facts(value,plan))
        q['source_requirement']='primary_only'
        self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_inference_uses_verified_premises_and_does_not_become_a_measured_effect(self):
        plan,pages,value=fixture();claim=value['items'][0]['claims'][0]
        claim.update(answer_type='inference',text='閲覧して候補を検討できます。')
        self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertEqual(value['evidence_ledger']['claims'][0]['answer_type'],'inference')
        self.assertTrue(needs_adjudication(value['items'][0],plan['items'][0]))
        plan['items'][0]['source_requirement']='primary_only'
        self.assertTrue(req.validate_matrix(value,plan,pages))

    def test_failed_fetch_cannot_be_used_as_explicit_non_disclosure(self):
        plan,pages,value=fixture();pages[0].update(status='failed',text='')
        self.assertTrue(req.validate_matrix(value,plan,pages))
        self.assertEqual(value['items'][0]['status'],'unresearched')

    def test_span_ids_expand_before_claim_evidence_indices_are_resolved(self):
        plan,pages,value=fixture();visible,index=indexed_sources(pages)
        for ref,page in zip(value['items'][0]['evidence'],visible):
            ref.pop('url');ref.pop('quote');ref.update(source_ref=page['excerpts'][0]['source_ref'],expert_source_ref='')
        expanded=expand_references(value,index)
        self.assertFalse(req.validate_matrix(expanded,plan,pages))
        self.assertEqual(expanded['items'][0]['claim_states'][0]['evidence'][0]['quote'],pages[0]['text'])

    def test_revalidation_keeps_original_check_time(self):
        plan,pages,value=fixture();req.validate_matrix(value,plan,pages)
        stamp=value['items'][0]['claims_checked_at'];req.validate_matrix(value,plan,pages)
        self.assertEqual(value['items'][0]['claims_checked_at'],stamp)

    def test_handoff_or_editorial_issue_does_not_trigger_source_collection(self):
        gaps=[dict(id='q1',reason='条件の引継ぎ',repair_kind='handoff'),dict(id='q2',reason='説明順序',repair_kind='editorial')]
        self.assertEqual(req.retrieval_gaps(gaps,{'items':[],'coverage_issues':[]},[]),[])
        self.assertEqual(req.retrieval_gaps([dict(id='q3',reason='根拠不足',repair_kind='source')],{'items':[]},[])[0]['id'],'q3')

    def test_new_authoring_intent_excludes_old_analysis_numbers_and_source_quotes(self):
        from test_reader_value_flow import fixtures
        from pipeline import reader_needs as rn,production_brief as pb
        brief,_,sources,value=fixtures();needs=rn.record(value,brief=brief,intent_sources=sources)
        with patch.object(pb,'load',return_value=brief),patch.object(rn,'load',return_value=needs):
            result=authoring_intent('j',{'content_text':'古い料金999万円','meta':{'reader_needs_version':rn.VERSION}})
        self.assertNotIn('999万円',result);self.assertNotIn('手段ごとの違いを知りたい',result)
        self.assertIn('自分に合う手段',result)

    def test_registered_marketing_claims_are_not_a_second_fact_source(self):
        source={'name':'A','url':'https://a.example','selling_points':['保証100%'],'raw_content':'古い料金','must_include':'比較で紹介'}
        filtered=registered_for_authoring(source,structured_intent=True)
        self.assertNotIn('selling_points',filtered);self.assertNotIn('raw_content',filtered)
        self.assertEqual(filtered['must_include'],'比較で紹介');self.assertIn('selling_points',source)

    def test_serp_prices_and_marketing_snippets_are_not_authoring_facts(self):
        from pipeline.generation_context import authoring_serp
        text=json.dumps({'organic_results':[dict(link='https://a.example',title='古い999円',snippet='未確認効果100%')]})
        result=authoring_serp(text,structured_intent=True)
        self.assertNotIn('999',result);self.assertNotIn('100%',result);self.assertIn('https://a.example',result)

    def test_source_mismatch_is_a_policy_recheck_and_not_a_retrieval_trigger(self):
        plan,pages,value=fixture();plan['items'][0]['source_requirement']='primary_only'
        value['items'][0]['claims'][0]['basis']='guidance'
        gaps=req.validate_matrix(value,plan,pages)
        self.assertEqual(gaps[0]['validation_issue']['code'],'source_basis_mismatch')
        with self.assertRaisesRegex(ContentQualityError,'根拠区分'):
            req.supplement_once('j','query',plan,gaps,matrix=value)

    def test_repair_packet_does_not_leak_withheld_claims_or_raw_competitor_bodies(self):
        plan,pages,value=fixture();unknown=copy.deepcopy(value['items'][0]['claims'][0])
        unknown.update(text='未確認の秘密機能',required_for_answer=False,disposition='withhold')
        value['items'][0]['claims'].append(unknown);req.validate_matrix(value,plan,pages)
        plan['competitor_context']={'body':'競合にしかない旧料金'}
        repaired=repair_requirements({'research_plan':plan,'research_decisions':value})
        text=json.dumps(repaired,ensure_ascii=False)
        self.assertNotIn('未確認の秘密機能',text);self.assertNotIn('競合にしかない旧料金',text)
        self.assertNotIn('evidence_ledger',repaired['research_decisions'])


def ranking_fixture():
    """Shape of saved tiered_screen_3 items.q03: 11 evidence spans, second claim cites 2..11."""
    chunks=[f'{n}位：間取りの後悔例{n}。'.ljust(256,'。') for n in range(1,12)]
    pages=[dict(url='https://b.example/ranking',status='success',text=''.join(chunks),fetched_at='2026-10-20T00:00:00Z')]
    plan={'items':[dict(id='q03',question='間取りの後悔ランキングの順位',subject='B',priority='essential',required=True,
        priority_reason='主回答',source_requirement='standard',official_check_required=False,requires_current=False)]}
    def claim(text,indices):
        return dict(text=text,subject='Bの順位記事',operation='順位を示す',answer_type='sourced',basis='primary',
            supports_current_conclusion=True,conditions=[dict(dimension='other',value='取材経験に基づく順位')],
            evidence_indices=indices,required_for_answer=True,disposition='publish',
            conflict=dict(status='none',evidence_indices=[],resolution=''))
    row=dict(reason='記事の1〜7位を確認',official_checked_urls=[],applicable_at='掲載情報',omission_reason='',claim_limits=[],
        exploration_complete=True,exploration_reason='順位記事を照合',
        claims=[claim('順位は取材経験に基づく。',[0,1]),claim('掲載順位は1位から7位まで。',list(range(2,12)))])
    return plan,pages,chunks,row


def ranking_response(request,template,second_indices):
    props=request['output_config']['format']['schema']['properties']
    if 'coverage_issues' in props:
        return {'coverage_sufficient':True,'coverage_reason':'主張と条件を照合','coverage_issues':[]}
    payload=json.loads(request['messages'][0]['content'])
    row=copy.deepcopy(template);row['claims'][1]['evidence_indices']=second_indices
    refs=[e['source_ref'] for page in payload['sources'] for e in page.get('excerpts',[])][:11]
    row['evidence']=[dict(source_ref=r,source_kind='primary',independence_group='b.example',expert_source_ref='',
                          expert_name='',expert_scope_reason='') for r in refs]
    if 'additional_sources_needed' in props['items']['properties']['q03']['properties']:
        row['additional_sources_needed']=False
    return {'items':{'q03':row},'coverage_sufficient':True,'coverage_reason':'原文を照合'}


class InvalidClaimReferenceRegressionTests(unittest.TestCase):
    BAD=list(range(2,12));FIXED=list(range(2,11))

    def test_saved_q03_shape_rejects_only_the_bad_claim_and_keeps_the_original(self):
        plan,pages,chunks,row=ranking_fixture()
        evidence=[dict(url=pages[0]['url'],quote=c,source_kind='primary',independence_group='b.example') for c in chunks]
        value={'coverage_sufficient':True,'coverage_reason':'照合','items':[{**copy.deepcopy(row),'id':'q03','evidence':evidence}]}
        gaps=req.validate_matrix(value,plan,pages)
        item=value['items'][0];first,second=item['claim_states']
        self.assertTrue(first['publishable']);self.assertEqual(len(first['evidence']),2)
        self.assertFalse(second['publishable']);self.assertEqual(second['evidence_indices'],self.BAD)
        self.assertEqual(second['reference_issue'],{'code':ce.REFERENCE_ISSUE,'claim_id':'q03.c02','evidence_count':11,
            'fields':[{'field':'evidence_indices','indices':self.BAD,'problems':['out_of_range']}]})
        self.assertIn('0〜10',second['validation_reason'])
        self.assertEqual(len(item['claim_evidence']),11)
        self.assertFalse(item['verified']);self.assertEqual(req.accepted_facts(value,plan),'')
        self.assertEqual(gaps[0]['validation_issue']['code'],ce.REFERENCE_ISSUE);self.assertIn('q03.c02',gaps[0]['reason'])
        # The same response with the maximum valid index passes unchanged policy.
        value['items'][0]=({**copy.deepcopy(row),'id':'q03','evidence':evidence})
        value['items'][0]['claims'][1]['evidence_indices']=self.FIXED
        self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertTrue(value['items'][0]['verified']);self.assertIn('7位まで',req.accepted_facts(value,plan))

    def run_focused(self,later_indices):
        from pipeline import tiered_research as tr
        plan,pages,_,template=ranking_fixture();calls=[];payloads={}
        def ask(job,step,request,**kwargs):
            calls.append(step);payloads[step]=json.loads(request['messages'][0]['content'])
            indices=self.BAD if step.startswith('tiered_screen') else later_indices
            return ranking_response(request,template,indices),SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':'focused'}),\
             patch.object(tr,'get_optional_artifact',return_value=None),patch.object(tr,'checked_request',side_effect=ask):
            value,_=tr.audit_matrix('j',plan,pages,'draft',{})
        return plan,pages,value,calls,payloads

    def test_bad_screen_reference_reaches_bounded_rereview_with_exact_finding(self):
        plan,pages,value,calls,payloads=self.run_focused(self.FIXED)
        self.assertEqual(calls,['tiered_screen_1','tiered_focused_1_reread','tiered_focused_1_audit','tiered_coverage'])
        candidate=payloads['tiered_focused_1_reread']['candidate_answers'][0]
        rejected=candidate['claim_states'][1]
        self.assertEqual(rejected['reference_issue']['fields'][0]['indices'],self.BAD)
        self.assertIn('範囲外',rejected['validation_reason'])
        self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertTrue(value['items'][0]['verified'])
        self.assertEqual(len(value['items'][0]['claim_states'][1]['evidence']),9)

    def test_still_invalid_after_rereview_fails_closed_without_more_requests_or_collection(self):
        plan,pages,value,calls,_=self.run_focused(self.BAD)
        self.assertEqual(calls,['tiered_screen_1','tiered_focused_1_reread','tiered_focused_1_audit','tiered_coverage'])
        gaps=req.validate_matrix(value,plan,pages);item=value['items'][0]
        self.assertFalse(item['verified']);self.assertEqual(req.accepted_facts(value,plan),'')
        self.assertEqual(item['claim_states'][1]['evidence_indices'],self.BAD)
        self.assertEqual(gaps[0]['validation_issue']['code'],ce.REFERENCE_ISSUE)
        with patch('pipeline.step_fact_sheet.run') as collect,patch.object(req,'upsert_artifact') as save:
            with self.assertRaisesRegex(ContentQualityError,'根拠参照'):
                req.supplement_once('j','query',plan,gaps,matrix=value)
            finding={**value,'plan_sha256':req.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
                     'coverage_issues':[{'id':'q03','reason':'順位の根拠','repair_kind':'source'}]}
            with self.assertRaisesRegex(ContentQualityError,'根拠参照'):
                req.resolve_current_findings('j','query',plan,finding)
        collect.assert_not_called();save.assert_not_called()

    def test_identical_rerun_reuses_every_saved_response(self):
        from contextlib import nullcontext
        from pipeline import tiered_research as tr
        plan,pages,_,template=ranking_fixture();store={};sent=[]
        def save(job_id,step,content_type,content_text,meta):
            store[step]={'step':step,'content_type':content_type,'content_text':content_text,'meta':copy.deepcopy(meta)}
        def create(client,**request):
            sent.append(request['model'])
            payload=json.loads(request['messages'][0]['content'])
            value=ranking_response(request,template,self.FIXED if 'candidate_answers' in payload else self.BAD)
            return SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(type='text',text=json.dumps(value,ensure_ascii=False))],
                                   usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        results=[]
        with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':'focused'}),\
             patch.object(tr,'get_optional_artifact',side_effect=lambda job,step:store.get(step)),\
             patch.object(tr,'upsert_artifact',side_effect=save),patch.object(tr,'create_with_retry',side_effect=create),\
             patch('pipeline.quality_budget.request_scope',side_effect=lambda *a,**k:nullcontext()):
            for _ in range(2):
                value,usage=tr.audit_matrix('j',plan,pages,'draft',{});results.append((value,usage))
                self.assertFalse(req.validate_matrix(value,plan,pages))
        self.assertEqual(len(sent),4)
        self.assertEqual((results[1][1].input_tokens,results[1][1].output_tokens),(0,0))
        self.assertEqual(results[1][0]['items'][0]['answer'],results[0][0]['items'][0]['answer'])


if __name__=='__main__':unittest.main()
