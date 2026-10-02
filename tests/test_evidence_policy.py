import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline.research_requirements import validate_matrix, validate_plan, accepted_facts
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

    def test_outline_audit_keys_trigger_research_instead_of_missing_id_error(self):
        plan,_,_=fixture();fresh=FreshSources({},[])
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1,output_tokens=2))
        with patch('pipeline.research_collection.upsert_artifact'),patch.object(fresh,'save'), \
             patch('pipeline.research_collection.get_optional_artifact',return_value=None), \
             patch('pipeline.research_collection.run_with_fetch',return_value=(response,'new',[],[])) as run:
            collect('j',None,plan=plan,fresh=fresh,system='',prompt='',model='test',search_tool={},
                gaps=json.dumps([{'key':'evidence_support','reason':'料金の根拠が不足'}]))
        self.assertIn('料金の根拠が不足',run.call_args.kwargs['prompt'])

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
