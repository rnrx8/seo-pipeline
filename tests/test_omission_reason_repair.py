import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import editorial_blueprint as bp,content_quality as cq
from pipeline.content_edits import content_blocks
from test_editorial_blueprint import fixture,artifact,response


class OmissionReasonRepairTests(unittest.TestCase):
    def setup_case(self):
        plan,pages,matrix,ctx,value=fixture()
        value['omissions']=[{'research_id':'q1','reason':'別の細目は原文で確認できないため省く'}]
        return plan,matrix,ctx,value,artifact(value,ctx)

    def test_actual_repair_changes_only_existing_reason_and_invalidates_qa(self):
        plan,matrix,ctx,value,a=self.setup_case()
        correction={'edits':[],'delete_ids':[],'section_order':[],
                    'omission_edits':[{'research_id':'q1','reason':'細目は原文にあるが今回の主回答では採用しない'}]}
        location=next(b['id'] for b in content_blocks(a['content_text']) if b['text'].startswith('- q1：'))
        report={'checks':[{'key':'evidence_support','status':'fail','reason':'省略理由の未確認という説明が原文と矛盾',
                          'affected_blocks':[{'id':location}]}]}
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.research_requirements.require_matrix',return_value=matrix))
            stack.enter_context(patch('pipeline.research_requirements.load_plan',return_value=plan))
            stack.enter_context(patch('pipeline.db.get_artifact',return_value={'content_text':'{"required_sections":[]}'}))
            stack.enter_context(patch('pipeline.db.get_job',return_value={}))
            saved=stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=lambda **kw:kw))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled',return_value=False))
            send=stack.enter_context(patch('pipeline.ai.create_with_retry',return_value=response(correction)))
            result=bp.repair('job','条件',report,a,None)
        repaired=bp.require_projection(result,ctx)
        self.assertEqual(repaired['sections'],value['sections'])
        self.assertEqual(repaired['titles'],value['titles'])
        self.assertEqual(repaired['omissions'],correction['omission_edits'])
        self.assertFalse(result['meta']['audited'])
        self.assertFalse(saved.call_args_list[-2].kwargs['meta']['valid'])
        self.assertEqual(json.loads(send.call_args.kwargs['messages'][0]['content'])['omission_reason_targets'],['q1'])

    def test_reason_permission_cannot_add_remove_or_change_question_ids(self):
        _,_,ctx,value,a=self.setup_case()
        base={'edits':[],'delete_ids':[],'section_order':[],'omission_edits':[]}
        for edits in ([{'research_id':'q9','reason':'追加'}],
                      [{'research_id':'q1','reason':''}],
                      [{'research_id':'q1','reason':'修正'},{'research_id':'q1','reason':'重複'}]):
            with self.subTest(edits=edits),self.assertRaises(cq.ContentQualityError):
                bp.apply_edits(value,{**base,'omission_edits':edits},ctx,structural=False,targets=[],omission_targets=['q1'])
        with self.assertRaises(cq.ContentQualityError):
            bp.apply_edits(value,{**base,'omission_edits':[{'research_id':'q1','reason':'無許可'}]},ctx,structural=False,targets=[])

    def test_exact_locations_do_not_unlock_unrelated_omission_reasons(self):
        _,_,_,_,a=self.setup_case();blocks=content_blocks(a['content_text'])
        location=next(b['id'] for b in blocks if b['text'].startswith('- q1：'))
        report={'checks':[{'key':'evidence_support','status':'fail','reason':'記録修正','affected_blocks':[{'id':location}]}]}
        self.assertEqual(bp.omission_repair_targets(a,report),['q1'])
        self.assertEqual(bp.repair_targets(a,report),[])
        report['checks'][0]['affected_blocks']=[{'id':blocks[0]['id']}]
        self.assertEqual(bp.omission_repair_targets(a,report),[])
        report['checks'][0].update(status='pass',affected_blocks=[])
        self.assertEqual(bp.omission_repair_targets(a,report),[])
