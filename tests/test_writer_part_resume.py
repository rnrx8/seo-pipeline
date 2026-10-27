"""A saved model response is reused by its complete request/material identity."""
import copy
import socket
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import step_article as writer


class WriterPartResumeTests(unittest.TestCase):
    def setUp(self):
        self.store={};self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')))
        self.stack.enter_context(patch.object(writer,'get_artifact',side_effect=lambda j,k:copy.deepcopy(self.store[k])))
        def save(**kw):self.store[kw['step']]=copy.deepcopy(kw);return copy.deepcopy(kw)
        self.stack.enter_context(patch.object(writer,'upsert_artifact',side_effect=save))
        self.stack.enter_context(patch.object(writer,'validate_delivery',side_effect=lambda *a,**k:[]))
        self.stack.enter_context(patch.object(writer,'scope_issues',return_value=[]))

    def run_part(self,facts='確認済み条件'):
        return writer._write_complete_part(None,[{'role':'user','content':'指定された章を執筆'}],2000,
            '### H2：比較',[('比較',1,300)],1,job_id='job',facts=facts)

    def partial(self,text):
        return writer.IncompleteArticleResponse(SimpleNamespace(stop_reason='max_tokens',
            content=[SimpleNamespace(type='text',text=text)],usage=SimpleNamespace(input_tokens=10,output_tokens=20)))

    def test_completed_response_is_reused_without_new_spend_or_changed_usage(self):
        with patch.object(writer,'_call',return_value=('## 比較\n\n確認済み条件から選びます。【PART1_END】',10,20)) as call:
            first=self.run_part()
        original=copy.deepcopy(self.store['article_part_1_response_1'])
        with patch.object(writer,'_call',side_effect=AssertionError('No second paid request')):
            second=self.run_part()
        self.assertEqual(first[0],second[0]);self.assertEqual(second[1:],(0,0))
        self.assertEqual(self.store['article_part_1_response_1'],original);self.assertEqual(call.call_count,1)

    def test_more_than_one_output_limit_completes_only_the_missing_suffix(self):
        replies=[self.partial('## 比較\n\n閲覧なら'),self.partial('スタンダード、送信なら'),
                 ('プレミアムを選びます。【PART1_END】',10,20)]
        with patch.object(writer,'_call',side_effect=replies) as call:
            text,_,_=self.run_part()
        self.assertEqual(text,'## 比較\n\n閲覧ならスタンダード、送信ならプレミアムを選びます。')
        self.assertEqual(call.call_count,3)
        for request in call.call_args_list[1:]:
            self.assertIn('未執筆部分だけ',request.args[1][-1]['content'])
        with patch.object(writer,'_call',side_effect=AssertionError('All received chunks are reusable')):
            resumed=self.run_part()
        self.assertEqual(resumed,(text,0,0))

    def test_material_change_requires_new_output_but_cannot_destroy_prior_receipt(self):
        with patch.object(writer,'_call',return_value=('## 比較\n\n対象の条件を説明します。',1,2)) as call:
            self.run_part('条件A');self.run_part('条件B')
        self.assertEqual(call.call_count,2)
        self.assertEqual(len([k for k in self.store if '_receipt_' in k]),2)
        with patch.object(writer,'_call',side_effect=AssertionError('Prior exact material still cached')):
            self.run_part('条件A')

    def test_changed_current_alias_does_not_destroy_immutable_paid_response(self):
        with patch.object(writer,'_call',return_value=('## 比較\n\n保存済みの本文。',1,2)):self.run_part()
        self.store['article_part_1_response_1']['content_text']='他の内容に変わった'
        with patch.object(writer,'_call',side_effect=AssertionError('Use verified immutable receipt')):
            self.assertIn('保存済みの本文',self.run_part()[0])
