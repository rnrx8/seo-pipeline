"""Mechanical edits are unaudited until the ordinary full article QA."""
import json,unittest,os
from unittest.mock import patch
from pipeline import prose_preflight as p,content_quality as cq,quality_budget as qb
from pipeline.content_edits import content_blocks
from pipeline.readability import readability_issues

class ProsePreflightTests(unittest.TestCase):
    def test_no_extra_paid_call_for_already_readable_prose(self):
        with patch('pipeline.tiered_research.checked_request') as sent:
            self.assertEqual(p.complete('j',None,text='# 題\n\n## 章\n\n短い回答。',check_candidate=lambda x:None),'# 題\n\n## 章\n\n短い回答。')
        sent.assert_not_called()

    def test_only_one_long_plain_paragraph_per_run_is_shortened_before_full_QA(self):
        text='# 題\n\n## 章\n\n'+'あ'*180+'\n\n'+'い'*160
        blocks=content_blocks(text);bid=blocks[-2]['id']
        def send(job,name,request,**kwargs):
            self.assertEqual(name,'tiered_article_prose_preflight')
            data=json.loads(request['messages'][0]['content'])
            self.assertEqual(data['target_block_ids'],[bid]);self.assertEqual(data['paragraph_limits'][bid],140)
            return {'edits':[{'id':bid,'new':'う'*140}]},None
        def check(t):self.assertEqual(readability_issues(t),[])
        with patch('pipeline.tiered_research.checked_request',side_effect=send),patch('pipeline.db.upsert_artifact') as save:
            candidate=p.complete('j',None,text=text,check_candidate=check)
        self.assertTrue(candidate.endswith('い'*160))
        receipt=json.loads(save.call_args.kwargs['content_text'])
        self.assertFalse(receipt['semantic_quality_verified']);self.assertNotIn('valid',receipt)
        self.assertFalse(save.call_args.kwargs['meta']['audited'])

    def test_rejected_optional_edit_preserves_manuscript_for_full_QA(self):
        text='# 題\n\n## 章\n\n'+'あ'*340;bid=content_blocks(text)[-1]['id']
        with patch('pipeline.tiered_research.checked_request',return_value=({'edits':[{'id':bid,'new':'新しい保証100%です。'}]},None)),patch('pipeline.db.upsert_artifact') as save:
            result=p.complete('j',None,text=text,check_candidate=lambda t:None)
        self.assertEqual(result,text)
        receipt=json.loads(save.call_args.kwargs['content_text'])
        self.assertEqual(receipt['status'],'rejected')
        self.assertEqual(receipt['next_action'],'ordinary_full_article_qa')
        self.assertFalse(receipt['semantic_quality_verified'])
        self.assertNotIn('valid',receipt)

    def test_rejected_mechanical_candidate_keeps_original_for_full_QA(self):
        text='# 題\n\n## 章\n\n'+'あ'*340;bid=content_blocks(text)[-1]['id']
        with patch('pipeline.tiered_research.checked_request',return_value=({'edits':[{'id':bid,'new':'あ'*320}]},None)) as send,patch('pipeline.db.upsert_artifact') as save:
            result=p.complete('j',None,text=text,check_candidate=lambda t:(_ for _ in ()).throw(cq.ContentQualityError('remaining overflow')))
        self.assertEqual(result,text);self.assertEqual(send.call_count,1)
        self.assertEqual(json.loads(save.call_args.kwargs['content_text'])['rejection_reason'],'remaining overflow')

    def test_provider_failure_is_not_swallowed_as_a_rejected_proposal(self):
        text='# 題\n\n## 章\n\n'+'あ'*340
        with patch('pipeline.tiered_research.checked_request',side_effect=cq.ContentQualityError('credit balance exhausted')),patch('pipeline.db.upsert_artifact') as save:
            with self.assertRaisesRegex(cq.ContentQualityError,'credit balance exhausted'):
                p.complete('j',None,text=text,check_candidate=lambda t:None)
        save.assert_not_called()

    def test_shortening_rhetorical_words_is_not_a_change_in_factual_conditions(self):
        text='# 題\n\n## 章\n\n必要なのは確かめ方を変えることです。'+'あ'*310;bid=content_blocks(text)[-1]['id']
        proposal={'edits':[{'id':bid,'new':'確かめ方を変えましょう。'}]}
        with patch('pipeline.tiered_research.checked_request',return_value=(proposal,None)),patch('pipeline.db.upsert_artifact') as save:
            result=p.complete('j',None,text=text,check_candidate=lambda t:self.assertEqual(readability_issues(t),[]))
        self.assertIn('確かめ方を変えましょう。',result)
        self.assertFalse(json.loads(save.call_args.kwargs['content_text'])['semantic_quality_verified'])

    def test_preflight_cannot_repeat_in_production_with_different_input(self):
        self.assertEqual(qb.review_operation('tiered_article_prose_preflight'),('article','prose_preflight',1))
        meta={'phase':'article','role':'prose_preflight','call_limit':1,'request_sha256':'new'}
        token=qb.REQUEST.set(meta)
        try:
            with patch.dict(os.environ,QUALITY_BUDGET_MODE='unlimited'),self.assertRaises(cq.ContentQualityError):
                qb.guard_review({'calls':[{'status':'accounted','cost_usd':.1,'reserved_usd':.1,'review_operation':{**meta,'request_sha256':'old'}}]},.1)
        finally:qb.REQUEST.reset(token)
