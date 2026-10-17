"""Internal receipt time must not spend another review; source changes must."""
import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pipeline import tiered_research as tr


class ReviewReceiptIdentityTests(unittest.TestCase):
    def test_clock_only_change_reuses_review_but_claim_or_source_change_does_not(self):
        request={'model':'gpt-6.1-sol','max_tokens':7400,'system':'independent review',
            'messages':[{'role':'user','content':json.dumps({'source_revision':'source-1','sources':[{'fetched_at':'source-date'}],
                'candidate_answers':[{'id':'q1','claims_checked_at':'receipt-time-1','claims':[{'text':'source-backed condition'}]}]})}]}
        records={};sent=[]
        def create(*args,**kwargs):
            sent.append(copy.deepcopy(kwargs))
            return NS(stop_reason='end_turn',content=[NS(type='text',text='{"complete":true}')],usage=NS(input_tokens=10,output_tokens=5))
        def save(**kw):records[kw['step']]=copy.deepcopy(kw)
        def changed(field,value):
            result=copy.deepcopy(request);payload=json.loads(result['messages'][0]['content'])
            if field=='clock':payload['candidate_answers'][0]['claims_checked_at']=value
            elif field=='claim':payload['candidate_answers'][0]['claims'][0]['text']=value
            else:payload['source_revision']=value
            result['messages'][0]['content']=json.dumps(payload);return result
        with patch.object(tr,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(records.get(s))), \
             patch.object(tr,'upsert_artifact',side_effect=save),patch.object(tr,'create_with_retry',side_effect=create):
            tr.checked_request('j','tiered_focused_1_audit',request)
            _,usage=tr.checked_request('j','tiered_focused_1_audit',changed('clock','receipt-time-2'))
            self.assertEqual(len(sent),1);self.assertEqual(usage.input_tokens,0)
            tr.checked_request('j','tiered_focused_1_audit',changed('claim','different condition'))
            tr.checked_request('j','tiered_focused_1_audit',changed('source','source-2'))
        self.assertEqual(len(sent),3)
        self.assertIn('claims_checked_at',json.loads(request['messages'][0]['content'])['candidate_answers'][0])
        payload=json.loads(sent[0]['messages'][0]['content'])
        self.assertNotIn('claims_checked_at',payload['candidate_answers'][0])
        self.assertEqual(payload['sources'][0]['fetched_at'],'source-date')
