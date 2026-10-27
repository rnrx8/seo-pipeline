"""Cause routing retains failed verdicts and cannot widen paid research."""
import copy
import json
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import upstream_routing as routing, content_quality as cq
from test_editorial_recovery import materials
from test_editorial_blueprint import fixture


class UpstreamRoutingTests(unittest.TestCase):
    def setUp(self):
        self.article,self.outline,self.report=materials()
        self.plan,_,self.matrix,_,_=fixture()
        self.report['snapshot']='saved-failed-qa'
        self.material=routing.packet(self.article,self.outline,self.report,self.plan,self.matrix)

    def test_only_addressed_chapter_dependencies_enter_the_route(self):
        self.assertEqual([s['id'] for s in self.material['sections']],['s01','s02'])
        self.assertEqual([q['id'] for q in self.material['questions']],['q1'])
        self.assertEqual(len(self.material['article_blocks']),1)
        self.assertNotIn('別の説明です。',json.dumps(self.material,ensure_ascii=False))
        self.assertTrue(self.material['confirmed_answers'])

    def test_each_finding_must_keep_a_real_minimal_dependency(self):
        good={'routes':[{'finding_index':0,'stage':'article','research_ids':['q1'],'reason':'保存済み条件の説明漏れ'}]}
        self.assertEqual(routing.checked(good,self.material),good['routes'])
        for change in ({'research_ids':['new-question']},{'finding_index':2},
                       {'stage':'research','research_ids':[]},{'research_ids':['q1','q1']}):
            value=copy.deepcopy(good);value['routes'][0].update(change)
            with self.subTest(change=change),self.assertRaises(cq.ContentQualityError):routing.checked(value,self.material)
        unknown=copy.deepcopy(self.material);unknown['confirmed_answers']=[]
        with self.assertRaises(cq.ContentQualityError):routing.checked(good,unknown)

    def test_saved_evidence_route_changes_work_destination_only(self):
        original=copy.deepcopy(self.report)
        value={'routes':[{'finding_index':0,'stage':'article','research_ids':['q1'],'reason':'確認済み条件を本文で説明する'}]}
        with patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')), \
             patch('pipeline.tiered_research.checked_request',return_value=(value,SimpleNamespace(input_tokens=1,output_tokens=1))) as send:
            routed,receipt=routing.resolve('job',self.article,self.outline,self.report,self.plan,self.matrix,1)
        self.assertEqual(self.report,original);self.assertFalse(routed['valid'])
        self.assertEqual(routed['snapshot'],original['snapshot'])
        self.assertEqual(routed['editorial_findings'][0]['problem'],original['editorial_findings'][0]['problem'])
        self.assertEqual(routed['editorial_findings'][0]['stage_to_fix'],'article')
        self.assertEqual(receipt['snapshot'],original['snapshot']);self.assertEqual(send.call_count,1)

    def test_stale_addresses_are_rejected_before_any_paid_request(self):
        self.article['content_text']+='\n\n## 変更された章\n\n別の本文。'
        with patch('pipeline.tiered_research.checked_request') as send:
            with self.assertRaises(cq.ContentQualityError):routing.resolve('job',self.article,self.outline,self.report,self.plan,self.matrix,1)
        send.assert_not_called()

    def test_missing_question_remains_reachable_when_outline_omitted_it(self):
        self.plan['items'].append({**self.plan['items'][0],'id':'q2'})
        self.matrix['items'].append({**self.matrix['items'][0],'id':'q2'})
        self.report['checks']=[{'key':'prose_quality','status':'fail','research_ids':['q2']}]
        value=routing.packet(self.article,self.outline,self.report,self.plan,self.matrix)
        self.assertEqual([q['id'] for q in value['questions']],['q1','q2'])
