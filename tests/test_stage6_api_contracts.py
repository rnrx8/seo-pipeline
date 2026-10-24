import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
from pipeline import editorial_blueprint as bp, focused_research as fr, tiered_research as tr
from pipeline import content_quality as cq, openai_review as oa, quality_budget as qb


class Stage6ApiContractTests(unittest.TestCase):
    def blueprint_review_request(self):
        ctx={'section_ids':['s'+str(i) for i in range(22)],'progression_section_ids':['s'+str(i) for i in range(7)],
             'reader_decisions':[{'id':'i'+str(i),'answer_requirements':['answer']*n} for i,n in enumerate([5,3,4,4])]}
        return {'model':'gpt-6.1-sol','max_tokens':6000,'system':'unchanged criteria',
                'messages':[{'role':'user','content':'unchanged design'}],
                'output_config':bp.review_config(cq.audit_output_config(cq.CHECKS),ctx)}

    def test_blueprint_review_capacity_covers_each_required_verdict(self):
        request=self.blueprint_review_request()
        self.assertEqual(tr.structured_review_output_limit(request),23200)
        self.assertEqual(request['max_tokens'],6000)
        self.assertEqual(tr.structured_review_output_limit({**request,'max_tokens':24000}),24000)
        ordinary={**request,'output_config':cq.audit_output_config(cq.CHECKS)}
        self.assertEqual(tr.structured_review_output_limit(ordinary),6000)

    def test_blueprint_capacity_keeps_completed_cache_and_changes_only_unsent_capacity(self):
        request=self.blueprint_review_request()
        with patch.object(tr,'cached_result',return_value={'completed':True}),patch.object(tr,'create_with_retry') as send:
            result,_=tr.checked_request('j','tiered_audit_research',request)
        self.assertTrue(result['completed']);send.assert_not_called()
        response=NS(stop_reason='end_turn',content=[NS(type='text',text='{}')],usage=NS(input_tokens=1,output_tokens=1))
        with patch.object(tr,'cached_result',return_value=None),patch.object(tr,'get_optional_artifact',return_value=None),patch.object(tr,'create_with_retry',return_value=response) as send,patch.object(tr,'upsert_artifact'):
            tr.checked_request('j','tiered_audit_research',request)
        expected={**request,'max_tokens':23200}
        self.assertEqual(send.call_args.kwargs,expected)

    def test_wide_research_adjudication_fits_observed_complete_response(self):
        from pipeline.research_requirements import MATRIX_SCHEMA
        from pipeline.source_spans import span_schema
        request={'model':'gpt-6.1-sol','max_tokens':10000,'system':'unchanged evidence criteria',
            'messages':[{'role':'user','content':'unchanged source excerpts and candidate claims'}],
            'output_config':span_schema(MATRIX_SCHEMA,['q'+str(i) for i in range(15)])}
        original=copy.deepcopy(request)
        def complete(*args,**sent):
            # The real 15-question subscription response used 11,000 tokens;
            # the corresponding native API request must be able to finish it.
            self.assertGreater(sent['max_tokens'],11000)
            self.assertEqual({k:v for k,v in sent.items() if k!='max_tokens'},
                             {k:v for k,v in original.items() if k!='max_tokens'})
            return NS(stop_reason='end_turn',content=[NS(type='text',text='{}')],
                      usage=NS(input_tokens=1,output_tokens=11000))
        with patch.object(tr,'cached_result',return_value=None), \
             patch.object(tr,'get_optional_artifact',return_value=None), \
             patch.object(tr,'upsert_artifact'),patch.object(tr,'create_with_retry',side_effect=complete) as send:
            tr.checked_request('j','tiered_adjudication_3',request)
        self.assertEqual(send.call_count,1)
        self.assertEqual(request,original)
        with patch.object(tr,'cached_result',return_value={'completed':True}),patch.object(tr,'create_with_retry') as send:
            result,_=tr.checked_request('j','tiered_adjudication_3',request)
        self.assertTrue(result['completed']);send.assert_not_called()

    def test_api_schema_uses_supported_bounds_and_keeps_literal_enums(self):
        raw=copy.deepcopy(bp.SCHEMA)
        sent=bp.OUTPUT_CONFIG['format']['schema']
        self.assertNotIn('maxItems',sent['properties']['titles'])
        self.assertEqual(raw,bp.SCHEMA)
        section=sent['properties']['sections']['items']['properties']
        self.assertNotIn('maximum',section['budget']['properties']['importance'])
        self.assertIn('maximum',section['budget']['properties']['importance']['description'])
        self.assertEqual(section['level']['enum'],bp.SECTION_SCHEMA['properties']['level']['enum'])
        repair=bp.repair_config()['format']['schema']['properties']['edits']['items']['properties']['section']['properties']
        self.assertNotIn('maximum',repair['budget']['properties']['importance'])

    def test_app_validation_keeps_title_and_budget_bounds_after_api_projection(self):
        with self.assertRaises(cq.ContentQualityError):bp.validate_shape(['a','b','c','d'],bp.SCHEMA['properties']['titles'])
        with self.assertRaises(cq.ContentQualityError):bp.validate_shape(6,bp.BUDGET_SCHEMA['properties']['importance'])
        with self.assertRaises(cq.ContentQualityError):bp.validate_shape(5,bp.SECTION_SCHEMA['properties']['level'])
        bp.validate_shape(['a','b','c'],bp.SCHEMA['properties']['titles'])

    def test_claim_count_not_just_question_count_reserves_review_output(self):
        candidates=[{'claims':[{}]*n,'claim_states':[{}]*n} for n in (2,3,4,2,2,1)]
        self.assertEqual(fr.review_output_limit('gpt-6.1-sol',7400,candidates),20000)
        self.assertEqual(fr.review_output_limit('gpt-6-luna',6000,candidates),7400)

    def test_capacity_change_reuses_complete_older_verdict_before_sending(self):
        request={'model':'gpt-6.1-sol','max_tokens':7400,'messages':[{'content':json.dumps({'candidate_answers':[{'claims':[{}]*14}]})}]}
        with patch.object(tr,'cached_result',return_value={'complete':True}),patch.object(tr,'create_with_retry') as send:
            result,usage=tr.checked_request('j','tiered_focused_1_audit',request)
        self.assertTrue(result['complete']);send.assert_not_called();self.assertEqual(usage.output_tokens,0)

    def test_incomplete_usage_is_recorded_but_not_returned_as_a_pass(self):
        class Response:
            status_code=200
            headers={}
            def iter_lines(self):
                event={'type':'response.incomplete','response':{'id':'resp_fixture','incomplete_details':{'reason':'max_output_tokens'},'usage':{'input_tokens':100,'output_tokens':1000,'output_tokens_details':{'reasoning_tokens':500}}}}
                yield 'data: '+json.dumps(event)
                yield ''
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'OPENAI_API_KEY':'offline','QUALITY_BUDGET_DIR':folder,'QUALITY_BUDGET_MODE':'unlimited'}),qb.scope('j'):
            with patch.object(oa.requests,'post',return_value=Response()):
                with self.assertRaises(cq.ContentQualityError):oa.create_review_response(model='gpt-6.1-sol',max_tokens=1000,system='check',messages=[{'role':'user','content':'x'}],output_config={'format':{'schema':{'type':'object'}}})
            with qb.ledger() as value:
                call=value['calls'][0]
                self.assertEqual(call['status'],'accounted')
                self.assertEqual(call['usage']['output_tokens'],1000)
                self.assertEqual(call['diagnostics']['incomplete_reason'],'max_output_tokens')
                self.assertEqual(call['usage']['output_tokens_details']['reasoning_tokens'],500)

    def test_wide_luna_reread_has_capacity_for_all_question_results(self):
        self.assertEqual(fr.review_output_limit('gpt-6-luna',6000,[{}]*17),17300)
        self.assertEqual(fr.review_output_limit('gpt-6-luna',6000,[{}]*2),6000)
        self.assertEqual(fr.review_output_limit('gpt-6-luna',6000,[{}]*40),18000)
