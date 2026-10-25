import copy,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pipeline import outline_completion as oc, editorial_blueprint as bp, quality_budget as qb
from pipeline.content_quality import ContentQualityError
from test_editorial_blueprint import fixture


class OutlineSuffixTests(unittest.TestCase):
    def setup_value(self):
        *_,ctx,value=fixture()
        request=dict(model='claude-opus-5-5',max_tokens=16000,system='design',messages=[{'role':'user','content':'current materials'}])
        partial=json.dumps({k:v for k,v in value.items() if k!='omissions'},ensure_ascii=False)[:-1]+',"omissions":[{"research_id":"q1","reason":"unfinished'
        artifact={'content_text':partial,'meta':{'request_sha256':oc.fingerprint(request),'stop_reason':'max_tokens','input_tokens':10,'output_tokens':20}}
        return ctx,value,request,artifact

    def test_suffix_preserves_every_complete_section_and_passes_original_validator(self):
        ctx,value,request,artifact=self.setup_value()
        with patch('pipeline.tiered_research.checked_request',return_value=({'omissions':[]},NS(input_tokens=2,output_tokens=3))) as send:
            result,_=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result,value)
        bp.validate(result,ctx)
        payload=json.loads(send.call_args.args[2]['messages'][0]['content'])
        self.assertEqual(list(payload['missing_schema']['properties']),['omissions'])
        self.assertEqual(payload['completed_fields']['sections'],value['sections'])
        self.assertEqual(send.call_args.args[2]['max_tokens'],8000)
        self.assertEqual(qb.review_operation('outline_suffix_completion'),('outline','suffix_completion',1))

    def test_complete_array_members_survive_truncation_and_invalid_member_is_not_adopted(self):
        _,value,_,artifact=self.setup_value()
        text=artifact['content_text'].split(',"omissions":')[0]+',"omissions":[{"research_id":"q1","reason":"kept"},{"research_id":"q2","reason":"cut'
        fixed,key,head,remaining,_=oc.prefix(text,bp.SCHEMA)
        self.assertEqual(key,'omissions');self.assertEqual(head,[{'research_id':'q1','reason':'kept'}])
        self.assertNotIn('omissions',fixed);self.assertEqual(fixed['sections'],value['sections'])
        self.assertEqual(list(remaining['properties']),['omissions'])

    def test_remaining_sections_can_be_appended_without_changing_completed_prefix(self):
        _,value,_,_=self.setup_value()
        fields={k:value[k] for k in ('titles','reader_focus','lead_direction')}
        text=json.dumps(fields,ensure_ascii=False)[:-1]+',"sections":['+json.dumps(value['sections'][0],ensure_ascii=False)+',{"id":"cut'
        fixed,key,head,schema,_=oc.prefix(text,bp.SCHEMA)
        self.assertEqual(key,'sections');self.assertEqual(head,value['sections'][:1])
        self.assertEqual(set(schema['properties']),{'sections','omissions'})
        self.assertEqual(schema['properties']['sections']['minItems'],0)

    def test_no_full_rewrite_without_complete_sections_and_no_stale_inputs(self):
        ctx,_,request,artifact=self.setup_value()
        with self.assertRaises(ContentQualityError):oc.prefix('{"titles":["unfinished',bp.SCHEMA)
        artifact['meta']['request_sha256']='stale'
        with patch('pipeline.tiered_research.checked_request') as send:
            with self.assertRaises(ContentQualityError):oc.complete('j',artifact,request,ctx,None)
        send.assert_not_called()

    def test_rejects_completion_that_rewrites_finished_fields(self):
        ctx,_,request,artifact=self.setup_value()
        with patch('pipeline.tiered_research.checked_request',return_value=({'omissions':[],'titles':['changed']},NS(input_tokens=1,output_tokens=1))):
            with self.assertRaises(ContentQualityError):oc.complete('j',artifact,request,ctx,None)

    def test_schema_complete_output_can_be_checked_without_another_model_request(self):
        ctx,value,request,artifact=self.setup_value();artifact['content_text']=json.dumps(value,ensure_ascii=False)
        with patch('pipeline.tiered_research.checked_request') as send:
            result,usage=oc.complete('j',artifact,request,ctx,None)
        self.assertEqual(result,value);send.assert_not_called();self.assertEqual(usage.output_tokens,0)
