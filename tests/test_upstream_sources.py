"""Saved source bodies are first; targeted collection never becomes all research."""
import copy
import json
import socket
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import upstream_sources as us, research_requirements as req
import test_partial_research_delivery as partial


class UpstreamSourceTests(unittest.TestCase):
    read=partial.PartialResearchDeliveryTests.read
    save=partial.PartialResearchDeliveryTests.save
    put_matrix=partial.PartialResearchDeliveryTests.put_matrix

    def setUp(self):
        partial.PartialResearchDeliveryTests.setUp(self)
        other=copy.deepcopy(self.plan['items'][0]);other['id']='q2';self.plan['items'].append(other)
        other=copy.deepcopy(self.matrix['items'][0]);other['id']='q2';self.matrix['items'].append(other)
        req.validate_matrix(self.matrix,self.plan,self.pages)
        self.matrix.update(valid=False,attempt=3)
        for name,fn in [('get_artifact',self.read),('get_optional_artifact',lambda j,k:copy.deepcopy(self.store.get(k))),
                        ('upsert_artifact',self.save)]:
            self.stack.enter_context(patch.object(us,name,side_effect=fn))
        self.stack.enter_context(patch.object(req,'require_authoring_matrix',side_effect=lambda j:copy.deepcopy(self.matrix)))

    def response(self,job,step,request):
        payload=json.loads(request['messages'][0]['content'])
        self.assertEqual([q['id'] for q in payload['plan']['items']],['q1'])
        self.assertEqual([i['id'] for i in payload['candidate_answers']],['q1'])
        self.requests.append((step,payload))
        row=copy.deepcopy(self.matrix['items'][0]);row['evidence']=copy.deepcopy(row['claim_evidence'])
        for ref in row['evidence']:
            page=next(p for p in payload['sources'] if p['url']==ref['url'])
            ref['source_ref']=next(e['source_ref'] for e in page['excerpts'] if ref['quote'] in e['text'])
            ref.pop('url');ref.pop('quote');ref.pop('expert_qualification_quote',None);ref['expert_source_ref']=''
        return {'items':[row],'coverage_sufficient':True,'coverage_reason':'対象の細目を照合済み','coverage_issues':[]},SimpleNamespace(input_tokens=1,output_tokens=1)

    def test_saved_sources_resolve_target_without_any_web_collection(self):
        self.requests=[];before=copy.deepcopy(self.matrix['items'][1])
        report={'editorial_findings':[{'stage_to_fix':'research','severity':'blocking','problem':'送信条件の引継ぎ漏れ'}]}
        with patch('pipeline.tiered_research.checked_request',side_effect=self.response), \
             patch('pipeline.step_fact_sheet.run',side_effect=AssertionError('Existing bodies suffice')) as fetch:
            result=us.run('job','条件',report,['q1'],1)
        fetch.assert_not_called();self.assertEqual(len(self.requests),1)
        self.assertEqual(result['items'][1]['claims'],before['claims'])
        self.assertEqual(result['items'][1]['answer'],before['answer'])
        self.assertFalse(result['valid']);self.assertFalse(result['coverage_sufficient'])
        self.assertTrue(result['items'][0]['verified'])
        self.assertIn('送信はプレミアムのみ',self.store['fact_sheet']['content_text'])
        self.assertEqual(result['facts_sha256'],req.digest(self.store['fact_sheet']['content_text']))

    def test_unknown_question_is_rejected_before_any_request(self):
        with patch('pipeline.tiered_research.checked_request') as send:
            with self.assertRaises(req.ContentQualityError):us.run('job','条件',{},['q999'],1)
        send.assert_not_called()

    def test_unsettled_collection_is_not_repeated(self):
        self.requests=[]
        self.store['article_upstream_source_collection']={'content_text':'{"status":"running","attempted_question_ids":["q1"]}'}
        with patch('pipeline.tiered_research.checked_request',side_effect=self.response),patch('pipeline.step_fact_sheet.run') as fetch:
            with self.assertRaises(req.ContentQualityError):us.run('job','条件',{'editorial_findings':[]},['q1'],2)
        fetch.assert_not_called();self.assertEqual(self.requests,[])
