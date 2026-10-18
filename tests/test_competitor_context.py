import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import competitor_context as cc,step_serp
from pipeline.content_quality import ContentQualityError


class CompetitorContextTests(unittest.TestCase):
    def test_browser_absence_and_unverified_empty_are_not_zero_questions(self):
        states=cc.enrichment({},'query',browser=True)
        self.assertEqual(states['people_also_ask']['status'],'not_captured')
        states=cc.enrichment({'people_also_ask':[]},'query',browser=True)
        self.assertEqual(states['people_also_ask']['status'],'unverified_empty')

    def test_explicit_empty_has_the_same_query_and_is_distinct_from_failure(self):
        raw={'people_also_ask':[],'enrichment_status':{'people_also_ask':{'query':'query','status':'collected'}}}
        self.assertEqual(cc.enrichment(raw,'query',browser=True)['people_also_ask']['status'],'collected_empty')
        raw['enrichment_status']['people_also_ask']['status']='fetch_failed'
        self.assertEqual(cc.enrichment(raw,'query',browser=True)['people_also_ask']['status'],'fetch_failed')

    def test_other_query_data_is_discarded_instead_of_used_as_a_fallback(self):
        raw={'people_also_ask':[{'question':'other query question'}],
             'enrichment_status':{'people_also_ask':{'query':'other','status':'collected'}}}
        state=cc.enrichment(raw,'query',browser=True)['people_also_ask']
        self.assertEqual(state['status'],'query_mismatch');self.assertEqual(state['items'],[])

    def test_api_missing_and_empty_are_different(self):
        self.assertEqual(cc.enrichment({},'query')['related_searches']['status'],'not_returned')
        self.assertEqual(cc.enrichment({'related_searches':[]},'query')['related_searches']['status'],'collected_empty')

    def test_explicit_not_supported_is_preserved(self):
        raw={'enrichment_status':{'related_searches':{'status':'not_supported'}}}
        self.assertEqual(cc.enrichment(raw,'query')['related_searches']['status'],'not_supported')

    def test_http_heading_fetch_also_keeps_original_body_without_another_request(self):
        body='<html><body><nav>無関係なナビ</nav><main><h2>探す方法</h2><p>'+('手段の違いを説明します。'*30)+'</p></main></body></html>'
        reply=SimpleNamespace(content=body.encode(),raise_for_status=lambda:None)
        with patch.object(step_serp,'get_public_page',return_value=reply) as fetch:
            result=step_serp._fetch_headings({'link':'https://a.example','title':'A'})
        self.assertEqual(fetch.call_count,1);self.assertEqual(result['fetch_status'],'success')
        self.assertIn('手段の違い',result['body_excerpt']);self.assertNotIn('無関係なナビ',result['body_excerpt'])
        self.assertIsNotNone(result['body_sha256']);self.assertIsNotNone(result['fetched_at'])

    def test_projection_is_bound_to_query_and_quotes_are_contiguous_saved_text(self):
        text=('無関係の説明。'*100)+'手段ごとの違いと向く条件を比べます。'+('補足説明。'*300)
        saved=cc.capture([dict(url='https://a.example',title='A',fetch_status='success',body_excerpt=text,body_truncated=True)],'探し方',{})
        projected=cc.project(saved,'探し方',{'selected':[{'question':'手段ごとの違いと向く条件'}]})
        self.assertLessEqual(len(projected['excerpts']),3)
        for ref in projected['excerpts']:
            self.assertEqual(ref['text'],text[ref['start']:ref['end']])
        with self.assertRaises(ContentQualityError):cc.project(saved,'違う検索')

    def test_body_fetch_failure_is_not_an_observed_content_gap(self):
        saved=cc.capture([dict(url='https://a.example',fetch_status='failed',failure_code='access_restricted')],'query',{})
        projected=cc.project(saved,'query');self.assertEqual(projected['status'],'not_available')
        self.assertEqual(projected['pages'][0]['fetch_status'],'failed');self.assertEqual(projected['excerpts'],[])

    def test_content_gap_is_an_evidenced_candidate_with_targeted_research_ids(self):
        context={'reader_needs':{'selected':[{'id':'i1'}]},'competitor_context':{'excerpts':[{'id':'p1:0','text':'方法名だけを掲載しています。'}]}}
        gap=dict(need_id='i1',question='どの手段が向くか',status='gap_candidate',reason='確認した箇所は方法名だけ',
                 opportunity='条件と違いを説明する',research_ids=['q1'],evidence=[dict(excerpt_id='p1:0',quote='方法名だけ')])
        plan={'items':[{'id':'q1'}],'content_gaps':[gap]};cc.validate_gaps(plan,context)
        gap['evidence'][0]['quote']='書かれていない引用'
        with self.assertRaises(ContentQualityError):cc.validate_gaps(plan,context)

    def test_missing_evidence_can_only_be_not_assessed(self):
        gap=dict(need_id='i1',question='どの手段が向くか',status='not_assessed',reason='本文未取得',opportunity='必要な回答を調べる',research_ids=['q1'],evidence=[])
        plan={'items':[{'id':'q1'}],'content_gaps':[gap]};context={'reader_needs':{'selected':[{'id':'i1'}]}}
        cc.validate_gaps(plan,context);gap['status']='gap_candidate'
        with self.assertRaises(ContentQualityError):cc.validate_gaps(plan,context)

    def test_partly_answered_need_can_keep_one_unassessed_record_with_scoped_evidence(self):
        context={'reader_needs':{'selected':[{'id':'i1'}]},
                 'competitor_context':{'excerpts':[{'id':'p1:0','text':'通知から知られる場合があります。'}]}}
        gap=dict(need_id='i1',question='身バレと法的リスクをどう考えるか',status='not_assessed',
                 reason='通知の説明は確認したが法的線引きは提示範囲外',opportunity='確認済みの範囲と未評価の部分を分ける',
                 research_ids=['q1'],evidence=[dict(excerpt_id='p1:0',quote='通知から知られる場合があります。')])
        plan={'items':[{'id':'q1'}],'content_gaps':[gap]};cc.validate_gaps(plan,context)
        plan['content_gaps'].append(dict(gap,status='answered_in_excerpt'))
        with self.assertRaises(ContentQualityError):cc.validate_gaps(plan,context)

    def test_serp_does_not_repeat_competitor_bodies_in_every_downstream_input(self):
        organic=[dict(position=i,title='A',link=f'https://a.example/{i}') for i in range(1,4)]
        records={}
        def save(**data):records[data['step']]=data;return {**data,'id':'saved'}
        with patch.object(step_serp,'get_job',return_value={}),patch.object(step_serp,'get_optional_artifact',return_value=None),\
             patch.object(step_serp,'fetch_serp',return_value=({'organic_results':organic},dict(provider='serpapi',query='query'))),\
             patch.object(step_serp,'_fetch_headings',side_effect=lambda item,*a:{'url':item['link'],'fetch_status':'success','body_excerpt':'本文だけの特徴','headings':[]}),\
             patch.object(step_serp,'upsert_artifact',side_effect=save):
            step_serp.run('j','query')
        self.assertIn('本文だけの特徴',records['competitor_evidence']['content_text'])
        self.assertNotIn('本文だけの特徴',records['serp']['content_text'])
        data=json.loads(records['serp']['content_text'])
        self.assertEqual(data['enrichment_status']['people_also_ask']['status'],'not_returned')


if __name__=='__main__':unittest.main()
