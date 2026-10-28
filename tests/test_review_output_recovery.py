import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from pipeline import openai_review as oa, tiered_research as tr, review_completion as rc
from pipeline import quality_budget as qb
from pipeline.content_quality import ContentQualityError
from pipeline.reader_decisions import review_schema, research_result


class ReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.decisions=[{'id':'i01','role':'primary','question':'選ぶには',
            'answer_requirements':['条件','違い'],'research_ids':['q01']}]
        self.schema=review_schema({'format':{'schema':{'type':'object','properties':{
            'coverage_sufficient':{'type':'boolean'},'coverage_reason':{'type':'string'},
            'coverage_issues':{'type':'array','items':{'type':'string'}}},
            'required':['coverage_sufficient','coverage_reason','coverage_issues'],'additionalProperties':False}}},self.decisions)
        self.request={'model':'gpt-6.1-sol','max_tokens':4000,'system':'source-backed review',
            'messages':[{'role':'user','content':json.dumps({'plan':{'items':[{}]*22}})}],
            'output_config':self.schema}
        self.verdict={'coverage_sufficient':False,'coverage_reason':'必要な違いが不明',
            'coverage_issues':['q01'],'decision_reviews':{'i01':{
                'status':'fail','reason':'違いが未確認','missing_requirements':[1],'research_ids':['q01']}}}

    def terminal(self,text,reason='max_output_tokens'):
        return {'id':'resp_offline','status':'incomplete','incomplete_details':{'reason':reason},
            'output':[{'type':'message','content':[{'type':'output_text','text':text}]}],
            'usage':{'input_tokens':100,'output_tokens':4000,'output_tokens_details':{'reasoning_tokens':2264}}}

    def test_standalone_decision_and_coverage_outputs_are_sized(self):
        self.assertEqual(tr.structured_review_output_limit(self.request),10000)
        larger=copy.deepcopy(self.request)
        larger['output_config']['format']['schema']['properties']['decision_reviews']['properties']['i01']['properties']['missing_requirements']['items']['maximum']=20
        self.assertEqual(tr.structured_review_output_limit(larger),14750)
        only=copy.deepcopy(self.request)
        del only['output_config']['format']['schema']['properties']['coverage_issues']
        self.assertGreater(tr.structured_review_output_limit(only),4000)

    def test_prefix_reuses_nested_complete_verdict_but_not_cut_text(self):
        text=json.dumps(self.verdict,ensure_ascii=False)
        schema=self.schema['format']['schema']
        cut=text.index('"違いが未確認"')+4
        fixed=rc.completed_fields(text[:cut],schema)
        self.assertFalse(fixed['coverage_sufficient'])
        self.assertEqual(fixed['decision_reviews'],{'i01':{'status':'fail'}})
        missing=rc.remaining_schema(schema,fixed)
        self.assertEqual(set(missing['properties']),{'decision_reviews'})
        self.assertEqual(set(missing['properties']['decision_reviews']['properties']['i01']['properties']),
                         {'reason','missing_requirements','research_ids'})

    def test_duplicate_unknown_and_overwriting_fields_rejected(self):
        schema=self.schema['format']['schema']
        for text in ('{"unknown":true,','{"coverage_sufficient":true,"coverage_sufficient":false,',
                     '{"coverage_sufficient":"true",'):
            with self.assertRaises(ContentQualityError):rc.completed_fields(text,schema)
        with self.assertRaises(ContentQualityError):rc.merge_fixed({'status':'fail'},{'status':'pass'})

    def test_stream_terminal_output_limit_keeps_usage_and_partial_without_retry(self):
        terminal=self.terminal('{"coverage_sufficient":false,')
        response=Mock(status_code=200,headers={'x-request-id':'req_offline'})
        response.iter_lines.return_value=['data: '+json.dumps({'type':'response.incomplete','response':terminal}),'']
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{
                'OPENAI_API_KEY':'offline','QUALITY_BUDGET_DIR':folder,'QUALITY_BUDGET_MODE':'unlimited'}),qb.scope('j'), \
             patch.object(oa.requests,'post',return_value=response) as post:
            with self.assertRaises(oa.OutputLimitResponse) as caught:oa.create_review_response(**self.request)
            self.assertEqual(caught.exception.response,terminal)
            with qb.ledger() as value:
                call=value['calls'][0]
                self.assertEqual(call['status'],'accounted')
                self.assertEqual(call['usage'],terminal['usage'])
                self.assertEqual(call['diagnostics']['incomplete_reason'],'max_output_tokens')
                self.assertNotIn('output',call['diagnostics'])
            post.assert_called_once();response.close.assert_called_once()

    def run_checked(self,send,saved):
        with patch.object(tr,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))), \
             patch.object(rc,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(saved.get(s))), \
             patch.object(tr,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:copy.deepcopy(kw)})), \
             patch.object(rc,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:copy.deepcopy(kw)})), \
             patch.object(tr,'create_with_retry',side_effect=send), \
             patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            return tr.checked_request('j','tiered_coverage',copy.deepcopy(self.request))

    def test_normal_path_completes_missing_fields_preserves_fail_and_reuses_after_restart(self):
        saved={};calls=[]
        prefix='{"coverage_sufficient":false,"coverage_reason":"必要な違いが不明","coverage_issues":["q01"],"decision_reviews":{"i01":{"status":"fail","reason":"途'
        def send(client,**request):
            calls.append(copy.deepcopy(request))
            if len(calls)==1:raise oa.OutputLimitResponse(self.terminal(prefix))
            self.assertEqual(request['messages'][:-1],calls[0]['messages'])
            fixed=json.loads(request['messages'][-1]['content'])['completed_review_readonly']
            self.assertFalse(fixed['coverage_sufficient'])
            self.assertEqual(fixed['decision_reviews']['i01'],{'status':'fail'})
            tail={'decision_reviews':{'i01':{k:v for k,v in self.verdict['decision_reviews']['i01'].items() if k!='status'}}}
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps(tail))],usage=NS(input_tokens=101,output_tokens=500))
        result,_=self.run_checked(send,saved)
        self.assertEqual(result,self.verdict)
        self.assertFalse(research_result(result,self.decisions)['coverage_sufficient'])
        self.assertEqual(len(calls),2)
        self.assertEqual(saved['tiered_coverage_incomplete']['content_text'],prefix)
        self.assertEqual(saved['tiered_coverage_incomplete']['meta']['usage']['output_tokens'],4000)
        self.assertGreater(calls[1]['max_tokens'],calls[0]['max_tokens'])
        value,usage=self.run_checked(Mock(side_effect=AssertionError('no duplicate generation')),saved)
        self.assertEqual(value,result);self.assertEqual(usage.output_tokens,0)

    def test_credit_transport_and_content_filter_do_not_send_completion(self):
        for error in (ContentQualityError('credit'),ContentQualityError('transport'),ContentQualityError('content_filter')):
            send=Mock(side_effect=error);saved={}
            with self.assertRaises(ContentQualityError):self.run_checked(send,saved)
            send.assert_called_once();self.assertFalse(saved)

    def test_no_visible_fields_adapts_once_then_is_bounded_durably(self):
        saved={};send=Mock(side_effect=oa.OutputLimitResponse(self.terminal('')))
        with self.assertRaisesRegex(ContentQualityError,'不足補完が進みません'):self.run_checked(send,saved)
        self.assertEqual(send.call_count,3)
        with self.assertRaises(ContentQualityError):self.run_checked(Mock(side_effect=AssertionError('no repeat')),saved)
        self.assertEqual(len(saved),3)

    def test_material_or_partial_changed_does_not_reuse(self):
        sized={**self.request,'max_tokens':tr.structured_review_output_limit(self.request)}
        artifact={'content_text':'{}','meta':{'completion_policy':rc.POLICY,'stop_reason':'max_tokens',
            'partial_sha256':rc.digest('{}'),'request_sha256':rc.fingerprint(sized)}}
        for changed in ({**sized,'system':'changed'},sized):
            bad=copy.deepcopy(artifact)
            if changed is sized:bad['content_text']='{"coverage_sufficient":true}'
            with patch.object(tr,'checked_request') as send,self.assertRaises(ContentQualityError):
                rc.recover_and_save('j','s',changed,bad,None,0)
            send.assert_not_called()
