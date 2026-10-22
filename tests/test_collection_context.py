import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import research_collection as collection
from pipeline.fresh_sources import FreshSources


class CollectionContextTests(unittest.TestCase):
    def plan(self):
        return {'items':[{'id':'q1','subject':'A','question':'広いサービスの使い方','required':True},
                         {'id':'q2','subject':'B','question':'独身限定サービスの登録条件','required':False},
                         {'id':'q3','subject':'C','question':'別の記事内判断の料金','required':False}],
                'reader_decisions':[{'id':'i1','question':'方法をどう選ぶか','scope':'広いサービスと独身限定を区別する',
                                     'research_ids':['q1','q2']},
                                    {'id':'i2','question':'費用をどう選ぶか','scope':'別の判断','research_ids':['q3']}]}

    def test_targeted_research_sees_relevant_neighbor_without_taking_its_work(self):
        plan=self.plan();before=copy.deepcopy(plan)
        result=collection.collection_context(plan,{'q1'})
        self.assertEqual([q['id'] for q in result['reader_questions']],['i1'])
        self.assertEqual(result['other_questions'],[{'id':'q2','subject':'B','question':'独身限定サービスの登録条件'}])
        self.assertEqual(plan,before)
        self.assertEqual(collection.collection_context({'items':plan['items']},{'q1'}),
                         {'reader_questions':[],'other_questions':[]})

    def test_context_reaches_collector_and_policy_change_invalidates_only_its_receipt(self):
        plan=self.plan();records={};fresh=FreshSources({},[])
        response=NS(usage=NS(input_tokens=1,output_tokens=1))
        def save(**kw):records[kw['step']]=copy.deepcopy(kw);return kw
        def run(*args,**kwargs):
            self.assertIn('広いサービスと独身限定を区別する',kwargs['prompt'])
            if '"subject": "A"' in kwargs['prompt']:
                self.assertIn('独身限定サービスの登録条件',kwargs['prompt'])
            return response,'調査メモ',[],[]
        with patch.object(collection,'tiered_review_enabled',return_value=False), \
             patch.object(collection,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
             patch.object(collection,'upsert_artifact',side_effect=save),patch.object(fresh,'save'), \
             patch.object(collection,'run_with_fetch',side_effect=run) as fetch:
            # q3 is unrelated and deliberately outside this collection fixture.
            plan['items']=plan['items'][:2]
            plan['reader_decisions']=plan['reader_decisions'][:1]
            collection.collect('j',None,plan=plan,fresh=fresh,system='policy',prompt='KW',model='test',search_tool={})
            self.assertEqual(fetch.call_count,2)
            collection.collect('j',None,plan=plan,fresh=fresh,system='policy',prompt='KW',model='test',search_tool={})
            self.assertEqual(fetch.call_count,2)
            with patch.object(collection,'COLLECTION_CONTEXT_VERSION','changed-context-policy'):
                collection.collect('j',None,plan=plan,fresh=fresh,system='policy',prompt='KW',model='test',search_tool={})
            self.assertEqual(fetch.call_count,4)
