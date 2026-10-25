import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import structured_completion as sc
from pipeline.content_quality import ContentQualityError, digest


class CompletionTests(unittest.TestCase):
    def request(self):
        schema={'type':'object','properties':{'sections':{'type':'object','properties':{
            's01':{'type':'string'},'s02':{'type':'string'}},'required':['s01','s02'],'additionalProperties':False}},
            'required':['sections'],'additionalProperties':False}
        return {'model':'claude-opus-5-5','max_tokens':12000,'system':'criteria',
            'messages':[{'role':'user','content':json.dumps({'target_section_ids':['s01','s02'],
                'target_sections':[{'id':'s01'},{'id':'s02'}]})}],
            'output_config':{'format':{'type':'json_schema','schema':schema}}}

    def artifact(self,request):
        return {'content_text':'{"sections":{"s01":"完成した説明。\\n条件は残す。","s02":"途中',
            'meta':{'stop_reason':'max_tokens','request_sha256':digest(json.dumps(request,ensure_ascii=False,sort_keys=True))}}

    def test_only_missing_values_are_sent_and_completed_values_remain_exact(self):
        request=self.request();artifact=self.artifact(request);original=copy.deepcopy((request,artifact))
        def send(job,step,suffix,client=None):
            self.assertEqual(step,'repair_suffix')
            payload=json.loads(suffix['messages'][0]['content'])
            self.assertEqual(payload['target_section_ids'],['s02'])
            self.assertEqual(payload['completed_sections_readonly'],{'s01':'完成した説明。\n条件は残す。'})
            return {'sections':{'s02':'完成した残り。'}},SimpleNamespace(input_tokens=1,output_tokens=1)
        with patch('pipeline.tiered_research.checked_request',side_effect=send) as sent:
            result,_,provenance=sc.recover('j','repair',request,artifact,None)
        self.assertEqual(result['sections']['s01'],'完成した説明。\n条件は残す。')
        self.assertEqual(provenance['preserved_ids'],['s01']);self.assertEqual(sent.call_count,1)
        self.assertEqual((request,artifact),original)

    def test_changed_request_or_invalid_completed_map_cannot_send(self):
        request=self.request();artifact=self.artifact(request)
        with patch('pipeline.tiered_research.checked_request') as sent:
            with self.assertRaises(ContentQualityError):sc.recover('j','repair',{**request,'system':'changed'},artifact,None)
            for text in ('{"sections":{"s01":"a","s01":"b",',
                         '{"sections":{"unknown":"a",', '{"sections":{"s01":"cut'):
                with self.assertRaises(ContentQualityError):sc.recover('j','repair',request,{**artifact,'content_text':text},None)
            sent.assert_not_called()

    def test_suffix_cannot_replace_a_completed_value(self):
        request=self.request();artifact=self.artifact(request)
        with patch('pipeline.tiered_research.checked_request',return_value=(
                {'sections':{'s01':'変更','s02':'残り'}},SimpleNamespace(input_tokens=1,output_tokens=1))):
            with self.assertRaises(ContentQualityError):sc.recover('j','repair',request,artifact,None)

    def test_complete_map_needs_no_generation(self):
        request=self.request();artifact=self.artifact(request);artifact['content_text']='{"sections":{"s01":"a","s02":"b"}}'
        with patch('pipeline.tiered_research.checked_request') as sent:
            value,_,_=sc.recover('j','repair',request,artifact,None)
            self.assertEqual(value['sections'],{'s01':'a','s02':'b'});sent.assert_not_called()
