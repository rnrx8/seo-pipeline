import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline.research_requirements import validate_matrix, validate_plan, accepted_facts, propose_optional_omissions, major_sources_checked
from pipeline.research_collection import collect
from pipeline.fresh_sources import FreshSources


def fixture(priority='essential'):
    plan={'items':[{'id':'q1','subject':'A','question':'料金','priority':priority,'priority_reason':'比較の根拠',
        'required':priority=='essential','source_requirement':'standard','requires_current':False}]}
    pages=[{'url':'https://official.example/price','status':'success','text':'料金の詳細は案内していません。'},
           {'url':'https://media-one.example/a','status':'success','text':'2025年1月の月額料金は2,000円です。'},
           {'url':'https://media-two.example/a','status':'success','text':'2025年1月の月額料金は2,000円です。'}]
    refs=[{'url':p['url'],'quote':p['text'],'source_kind':'secondary','independence_group':p['url']} for p in pages[1:]]
    value={'coverage_sufficient':True,'coverage_reason':'主要な料金比較に回答できる','items':[
        {'id':'q1','status':'confirmed','answer':'2025年1月時点で月額2,000円','reason':'条件と時点が一致',
         'basis':'corroborated','official_checked_urls':[pages[0]['url']],'applicable_at':'2025年1月',
         'supports_current_conclusion':False,'omission_reason':'','exploration_complete':True,'exploration_reason':'関連公式資料と独立メディア本文を確認','evidence':refs}]}
    return plan,pages,value


class EvidencePolicyTests(unittest.TestCase):
    def test_current_corroboration_does_not_require_matching_start_dates(self):
        plan,pages,value=fixture()
        for page in pages[1:]:page['text']='男性の1ヶ月契約は月額2,000円です。'
        item=value['items'][0]
        item.update(answer='男性の1ヶ月契約は月額2,000円。',applicable_at='今回確認した第三者本文の掲載情報',
                    supports_current_conclusion=True)
        for ref,page in zip(item['evidence'],pages[1:]):ref['quote']=page['text']
        self.assertEqual(validate_matrix(value,plan,pages),[])
        # Two records from the same publisher are still not corroboration.
        for ref in item['evidence']:ref['independence_group']='same-publisher'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_undated_current_listing_does_not_become_an_effective_date_requirement(self):
        plan,pages,value=fixture()
        item=value['items'][0]
        item.update(applicable_at='',supports_current_conclusion=True)
        for page in pages[1:]:page['text']='個人プランは月額2,000円です。'
        item['answer']='個人プランは月額2,000円。'
        for ref,page in zip(item['evidence'],pages[1:]):ref['quote']=page['text']
        self.assertEqual(validate_matrix(value,plan,pages),[])
        text=accepted_facts(value,plan)
        self.assertIn('根拠の時点・条件：',text)
        self.assertNotIn('適用時点：',text)
        self.assertIn('確認日：',text)
        # An explicitly historical claim still needs its historical period.
        item['basis']='historical'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_qualified_answer_keeps_current_use_restriction(self):
        plan,pages,value=fixture()
        self.assertEqual(validate_matrix(value,plan,pages),[])
        text=accepted_facts(value,plan)
        self.assertIn('2025年1月',text)
        self.assertIn('現在の比較結論には使用不可',text)
        plan['items'][0]['requires_current']=True
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_all_stages_receive_the_same_acceptance_contract(self):
        from pipeline.evidence_policy import EVIDENCE_POLICY
        from pipeline.research_requirements import MATRIX_SYSTEM, PLAN_SYSTEM
        from pipeline.research_verification import COVERAGE_SYSTEM
        from pipeline.content_quality import AUDIT_SYSTEM, EDITORIAL_SYSTEM
        from pipeline.fresh_sources import DIRECT_POLICY, WRITING_POLICY
        for prompt in (MATRIX_SYSTEM,PLAN_SYSTEM,COVERAGE_SYSTEM,AUDIT_SYSTEM,EDITORIAL_SYSTEM,DIRECT_POLICY,WRITING_POLICY):
            with self.subTest(prompt=prompt[:30]):self.assertIn(EVIDENCE_POLICY,prompt)

    def test_optional_omission_needs_major_sources_and_overall_acceptance_not_exhaustion(self):
        plan,pages,value=fixture('supporting');item=value['items'][0]
        item.update(status='unresearched',basis='unresolved',exploration_complete=False)
        validate_matrix(value,plan,pages)
        propose_optional_omissions(value['items'],plan,pages)
        self.assertEqual(item['basis'],'omitted')
        self.assertFalse(item['exploration_complete'])
        self.assertEqual(item['answer'],'')
        self.assertEqual(item['evidence'],[])
        self.assertFalse(validate_matrix(value,plan,pages))
        self.assertEqual(accepted_facts(value,plan),'')
        value.update(coverage_sufficient=False,coverage_reason='比較の重要論点が足りない',coverage_issues=[{'id':'q1','reason':'このクエリでは必要'}])
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_omission_does_not_excuse_required_unread_or_unvisited_sources(self):
        for case in ('required','unread','unvisited','no_record'):
            plan,pages,value=fixture('supporting');item=value['items'][0]
            item.update(status='unresearched',basis='unresolved',verified=False,exploration_complete=False)
            if case=='required':plan['items'][0]['required']=True
            if case=='unread':
                for page in pages:page['status']='failed'
            if case=='unvisited':item['official_checked_urls']=['https://invented.example']
            if case=='no_record':item['exploration_reason']=''
            propose_optional_omissions(value['items'],plan,pages)
            self.assertEqual(item['basis'],'unresolved',case)
            self.assertTrue(validate_matrix(value,plan,pages),case)

    def test_optional_omission_with_failed_official_and_read_secondary(self):
        plan,pages,value=fixture('supporting');item=value['items'][0]
        pages[0].update(status='failed',reason='access denied')
        item.update(status='unresearched',basis='unresolved',verified=False,exploration_complete=False)
        propose_optional_omissions(value['items'],plan,pages)
        self.assertEqual(item['basis'],'omitted')
        self.assertEqual(validate_matrix(value,plan,pages),[])
        self.assertEqual(accepted_facts(value,plan),'')
        value.update(coverage_sufficient=False,coverage_reason='主要な回答が不足',coverage_issues=[{'id':'q1','reason':'主要な判断に必要'}])
        self.assertTrue(validate_matrix(value,plan,pages))
        for page in pages[1:]:page['text']='unrelated text'
        self.assertFalse(major_sources_checked(item,pages))

    def test_verified_optional_fact_is_not_discarded(self):
        plan,pages,value=fixture('supporting')
        validate_matrix(value,plan,pages)
        original=copy.deepcopy(value['items'])
        propose_optional_omissions(value['items'],plan,pages)
        self.assertEqual(value['items'],original)

    def test_general_optional_term_can_be_omitted_after_recorded_reread(self):
        plan,pages,value=fixture('supporting');item=value['items'][0]
        plan['items'][0]['subject']='共通'
        item.update(status='unresearched',basis='unresolved',verified=False,
            answer='',evidence=[],official_checked_urls=[],reviewed_source_urls=[pages[1]['url']])
        propose_optional_omissions(value['items'],plan,pages)
        self.assertEqual(item['basis'],'omitted')
        self.assertEqual(validate_matrix(value,plan,pages),[])
        self.assertEqual(accepted_facts(value,plan),'')
        item['reviewed_source_urls']=['https://not-present.example']
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_subject_main_source_can_support_omission_without_per_item_search(self):
        plan,pages,value=fixture()
        donor=value['items'][0]
        donor.update(basis='primary',answer='公式に確認済み',evidence=[{
            'url':pages[0]['url'],'quote':pages[0]['text'],'source_kind':'primary'}])
        plan['items'].append({**plan['items'][0],'id':'q2','required':False,'priority':'supporting','question':'補助的な属性'})
        optional={**copy.deepcopy(donor),'id':'q2','status':'unresearched','basis':'unresolved',
                  'official_checked_urls':[],'exploration_reason':'','exploration_complete':False,'evidence':[]}
        value['items'].append(optional)
        validate_matrix(value,plan,pages)
        original=copy.deepcopy(value)
        propose_optional_omissions(value['items'],plan,pages)
        self.assertEqual(optional['basis'],'omitted')
        self.assertEqual(optional['omission_source_review']['source_question_ids'],['q1'])
        self.assertFalse(optional['exploration_complete'])
        self.assertFalse(validate_matrix(value,plan,pages))
        for subject in ('B','共通'):
            different=copy.deepcopy(plan);different['items'][1]['subject']=subject
            v=copy.deepcopy(original)
            propose_optional_omissions(v['items'],different,pages)
            self.assertEqual(v['items'][1]['basis'],'unresolved')

    def test_overall_contradictions_are_routed_even_with_other_local_gaps(self):
        plan,pages,value=fixture()
        plan['items'].append({**plan['items'][0],'id':'q2'})
        value['items'].append({**copy.deepcopy(value['items'][0]),'id':'q2','status':'unresearched','basis':'unresolved'})
        value.update(coverage_sufficient=False,coverage_issues=[{'id':'q1','reason':'比較条件が他社と不一致'}])
        gaps=validate_matrix(value,plan,pages)
        self.assertEqual({g['id'] for g in gaps},{'q1','q2'})
        self.assertIn('比較条件',next(g['reason'] for g in gaps if g['id']=='q1'))
        for issue in ({'id':'unknown','reason':'unknown'}, {'id':'q1','reason':''}):
            value['coverage_issues']=[issue]
            with self.assertRaises(ValueError):validate_matrix(value,plan,pages)

    def test_commercial_secondary_sources_can_support_dated_claim(self):
        plan,pages,value=fixture()
        self.assertFalse(validate_matrix(value,plan,pages))
        self.assertIn('2025年1月',accepted_facts(value,plan))
        self.assertIn('現在の比較結論には使用不可',accepted_facts(value,plan))

    def test_syndicated_articles_do_not_count_as_independent(self):
        plan,pages,value=fixture()
        for r in value['items'][0]['evidence']:r['independence_group']='same-original'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_one_domain_and_unvisited_official_claims_fail(self):
        plan,pages,value=fixture()
        value['items'][0]['official_checked_urls']=['https://invented.example/']
        self.assertTrue(validate_matrix(value,plan,pages))
        plan,pages,value=fixture()
        pages[2]['url']='https://media-one.example/b';value['items'][0]['evidence'][1]['url']=pages[2]['url']
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_specialist_requirement_cannot_be_overridden_by_secondary_consensus(self):
        plan,pages,value=fixture();plan['items'][0]['source_requirement']='primary_only'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_historical_price_cannot_prove_current_cheapest(self):
        plan,pages,value=fixture();value['items'][0]['basis']='historical'
        self.assertFalse(validate_matrix(value,plan,pages))
        plan['items'][0]['requires_current']=True
        self.assertTrue(validate_matrix(value,plan,pages))
        plan['items'][0]['requires_current']=False;value['items'][0]['applicable_at']=''
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_missing_optional_fact_needs_recorded_exploration_and_omission_reason(self):
        plan,pages,value=fixture('supporting');i=value['items'][0]
        i.update(status='searched_not_found',basis='omitted',answer='',evidence=[],omission_reason='地域分布は補助。料金と機能で選択できる。')
        self.assertFalse(validate_matrix(value,plan,pages))
        self.assertEqual(accepted_facts(value,plan),'')
        for field,new in [('official_checked_urls',[]),('omission_reason',''),('status','unresearched')]:
            bad=copy.deepcopy(value);bad['items'][0][field]=new
            self.assertTrue(validate_matrix(bad,plan,pages))

    def test_essential_cannot_be_omitted_or_satisfied_by_non_disclosure(self):
        plan,pages,value=fixture();i=value['items'][0]
        i.update(status='explicitly_undisclosed',basis='omitted',omission_reason='非公開と記載')
        self.assertTrue(validate_matrix(value,plan,pages))
        i['basis']='primary';i['evidence']=[{'url':pages[0]['url'],'quote':pages[0]['text'],'source_kind':'primary','independence_group':'official'}]
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_individual_omissions_cannot_hide_overall_coverage_failure(self):
        plan,pages,value=fixture('important');value['items'][0].update(status='searched_not_found',basis='omitted',omission_reason='見つからない')
        value['coverage_sufficient']=False;value['coverage_reason']='省略が多すぎて比較不能'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_priority_conflict_rejected_before_research(self):
        plan,_,_=fixture();validate_plan(plan)
        plan['items'][0]['required']=False
        with self.assertRaises(ValueError):validate_plan(plan)

    def test_primary_feature_can_be_accepted_without_competitor_evidence(self):
        plan,pages,value=fixture();plan['items'][0]['question']='Aはスタンド付きか'
        pages[0]['text']='本製品にはスタンドを搭載しています。'
        value['items'][0].update(answer='Aはスタンド付き',basis='primary',evidence=[{
            'url':pages[0]['url'],'quote':pages[0]['text'],'source_kind':'primary','independence_group':'A'}])
        self.assertFalse(validate_matrix(value,plan,pages))

    def test_invalid_quote_still_fails_secondary_route(self):
        plan,pages,value=fixture();value['items'][0]['evidence'][0]['quote']='料金は1,000円'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_scoped_retry_preserves_unaffected_research_and_saves_each_subject(self):
        plan,_,_=fixture();other=copy.deepcopy(plan['items'][0]);other.update(id='q2',subject='B');plan['items'].append(other)
        fresh=FreshSources({},[]);saved={}
        def save(**kw):saved[kw['step']]=kw;return kw
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1,output_tokens=2))
        with patch('pipeline.research_collection.upsert_artifact',side_effect=save),patch.object(fresh,'save'), \
             patch('pipeline.research_collection.get_optional_artifact',side_effect=lambda j,s:saved.get(s)), \
             patch('pipeline.research_collection.run_with_fetch',side_effect=[(response,'A original',[],[]),(response,'B original',[],[]),(response,'B updated',[],[])]) as run:
            args=dict(plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={})
            collect('j',None,**args)
            _,text,_,_=collect('j',None,**args,gaps=json.dumps([{'id':'q2','reason':'不足'}]))
        self.assertEqual(run.call_count,3)
        self.assertIn('A original',text);self.assertIn('B updated',text);self.assertNotIn('B original',text)
        self.assertEqual(saved['research_collection_2']['meta']['question_ids'],['q2'])

    def test_changed_plan_retrieves_only_flagged_question_and_keeps_search_history(self):
        plan,_,_=fixture();plan['items'] += [{**plan['items'][0],'id':'q2'},{**plan['items'][0],'id':'q3','subject':'B'}]
        fresh=FreshSources({},[])
        records={'research_collection_1':{'content_text':'A notes','meta':{'subject':'A','plan_sha256':'old','search_queries':['old query']}},
            'research_collection_2':{'content_text':'B notes','meta':{'subject':'B','plan_sha256':'old'}}}
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1,output_tokens=2))
        with patch('pipeline.research_collection.upsert_artifact') as save,patch.object(fresh,'save'), \
             patch('pipeline.research_collection.get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch('pipeline.research_collection.run_with_fetch',return_value=(response,'A new',['new query'],[])) as run:
            _,text,_,_=collect('j',None,plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},gaps=json.dumps([{'id':'q2'}]))
        self.assertEqual(run.call_count,1)
        self.assertIn('"id": "q2"',run.call_args.kwargs['prompt'])
        self.assertNotIn('"id": "q1"',run.call_args.kwargs['prompt'])
        self.assertIn('B notes',text)
        self.assertEqual(next(c.kwargs['meta']['search_queries'] for c in save.call_args_list if c.kwargs['step']=='research_collection_1'),['old query','new query'])

    def test_unscoped_gap_cannot_trigger_all_subjects_research(self):
        plan,_,_=fixture();fresh=FreshSources({},[])
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1,output_tokens=2))
        with patch('pipeline.research_collection.upsert_artifact'),patch.object(fresh,'save'), \
             patch('pipeline.research_collection.get_optional_artifact',return_value=None), \
             patch('pipeline.research_collection.run_with_fetch',return_value=(response,'new',[],[])) as run:
            with self.assertRaises(ValueError):
                collect('j',None,plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},
                    gaps=json.dumps([{'key':'evidence_support','reason':'料金の根拠が不足'}]))
        run.assert_not_called()

    def test_separate_source_checks_cannot_override_global_coverage_failure(self):
        from pipeline.research_verification import audit_matrix
        from pipeline import research_verification as verify
        plan,pages,value=fixture()
        def msg(v):return SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps(v))],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(verify,'get_optional_artifact',return_value=None),patch.object(verify,'upsert_artifact'), \
             patch.object(verify,'create_with_retry',side_effect=[msg(value),msg({'coverage_sufficient':False,'coverage_reason':'重要な選択材料が不足'})]):
            result,usage=audit_matrix('j',None,plan,pages,'facts','test',100)
        self.assertTrue(validate_matrix(result,plan,pages))
        self.assertEqual(usage.input_tokens,2)

    def test_checked_middle_quote_survives_shared_source_excerpt(self):
        from pipeline.content_quality import source_evidence
        quote='本人確認後に書類を即時破棄します。'
        page={'url':'https://official.example','status':'success','text':'前'*100000+quote+'後'*100000,'evidence_quotes':[quote,'捏造された記述']}
        result=json.loads(source_evidence({'content_text':json.dumps([page])}))[0]
        self.assertIn(quote,result['text'])
        self.assertNotIn('捏造された記述',result['text'])
        self.assertTrue(result['truncated'])

    def test_one_missing_answer_does_not_retry_every_completed_subject(self):
        plan,pages,value=fixture()
        other=copy.deepcopy(plan['items'][0]);other.update(id='q2',priority='supporting',required=False)
        plan['items'].append(other)
        omission=copy.deepcopy(value['items'][0]);omission.update(id='q2',status='searched_not_found',basis='omitted',answer='',evidence=[],omission_reason='補助的な地域分布は回答に不可欠でない')
        value['items'].append(omission)
        value['items'][0].update(status='unresearched',basis='unresolved')
        value['coverage_sufficient']=False;value['coverage_reason']='q1の必須料金が不足'
        self.assertEqual([g['id'] for g in validate_matrix(value,plan,pages)],['q1'])

    def test_invalid_plan_is_bounded_and_cannot_leave_an_old_ready_plan(self):
        from pipeline import research_requirements as r
        plan,_,_=fixture()
        response=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps(plan))],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        saved={}
        def save(**kw):saved[kw['step']]=kw;return kw
        with patch.object(r,'get_job',return_value={}),patch.object(r,'get_artifact',return_value={'content_text':'検索意図'}), \
             patch.object(r.anthropic,'Anthropic'),patch.object(r,'create_with_retry',return_value=response) as generate, \
             patch.object(r,'upsert_artifact',side_effect=save), \
             patch('pipeline.research_plan_review.review',return_value=({'valid':False,'issues':[{'id':'q1','reason':'補助条件を必須へ抱き合わせている'}]},response.usage)):
            with self.assertRaises(ValueError):r.plan('j','おすすめ')
        self.assertEqual(generate.call_count,2)
        self.assertIn('planning_issues',json.loads(generate.call_args.kwargs['messages'][0]['content']))
        self.assertFalse(json.loads(saved['research_plan']['content_text'])['valid'])
        self.assertFalse(json.loads(saved['research_matrix']['content_text'])['valid'])

    def test_plan_is_published_only_after_independent_review_passes(self):
        from pipeline import research_requirements as r
        plan,_,_=fixture()
        response=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps(plan))],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(r,'get_job',return_value={}),patch.object(r,'get_artifact',return_value={'content_text':'検索意図'}), \
             patch.object(r.anthropic,'Anthropic'),patch.object(r,'create_with_retry',return_value=response), \
             patch.object(r,'upsert_artifact',side_effect=lambda **kw:kw), \
             patch('pipeline.research_plan_review.review',return_value=({'valid':True,'issues':[]},response.usage)):
            saved=r.plan('j','おすすめ')
        self.assertTrue(saved['meta']['planning_review_passed'])
        self.assertEqual(json.loads(saved['content_text'])['policy_sha256'],r.plan_policy())


class ExpertEvidenceTests(unittest.TestCase):
    def fixture(self):
        plan,pages,value=fixture('supporting')
        plan['items'][0].update(question='検索意図に対応する一般的な注意点',source_requirement='expert_allowed')
        pages=[{'url':'https://expert.example/faq','status':'success',
                'text':'執筆：弁護士 山田太郎。個別の事情によって判断が異なります。'}]
        value['items'][0].update(basis='expert',answer='個別の事情によって判断が異なる',official_checked_urls=[],
            evidence=[{'url':pages[0]['url'],'quote':'個別の事情によって判断が異なります。',
                       'source_kind':'secondary','independence_group':'author-yamada',
                       'expert_name':'山田太郎','expert_qualification_quote':'執筆：弁護士 山田太郎。',
                       'expert_scope_reason':'弁護士による一般的な説明。特定サービスの適法性を保証しない。'}])
        return plan,pages,value

    def test_general_expert_explanation_does_not_require_statute_or_two_media(self):
        plan,pages,value=self.fixture()
        self.assertFalse(validate_matrix(value,plan,pages))
        self.assertIn('採用根拠：expert',accepted_facts(value,plan))

    def test_same_explanation_cannot_satisfy_original_source_requirement(self):
        plan,pages,value=self.fixture()
        plan['items'][0].update(question='特定の判決の内容',source_requirement='primary_only')
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_invented_or_missing_expert_credentials_fail(self):
        for field,new in [('expert_name','別人'),('expert_qualification_quote','執筆：弁護士 別人。'),
                          ('expert_qualification_quote',''),('expert_scope_reason','')]:
            with self.subTest(field=field,new=new):
                plan,pages,value=self.fixture();value['items'][0]['evidence'][0][field]=new
                self.assertTrue(validate_matrix(value,plan,pages))

    def test_media_consensus_cannot_replace_qualified_expert(self):
        plan,pages,value=fixture();plan['items'][0]['source_requirement']='expert_allowed'
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_expert_claim_still_needs_exact_support_and_current_applicability(self):
        plan,pages,value=self.fixture();value['items'][0]['evidence'][0]['quote']='絶対に合法です。'
        self.assertTrue(validate_matrix(value,plan,pages))
        plan,pages,value=self.fixture();plan['items'][0]['requires_current']=True
        self.assertTrue(validate_matrix(value,plan,pages))

    def test_historical_expert_evidence_keeps_time_limit(self):
        plan,pages,value=self.fixture();value['items'][0]['basis']='historical'
        self.assertFalse(validate_matrix(value,plan,pages))
        value['items'][0]['applicable_at']=''
        self.assertTrue(validate_matrix(value,plan,pages))
