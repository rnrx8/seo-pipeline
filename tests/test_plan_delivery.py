"""Exercise the real plan producer and preflight, not a mocked plan stage."""
import copy,json,socket,unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from pipeline import research_requirements as req,research_plan_review as review,quality_warnings as q
from pipeline.step_plan import research_resume_stage
from pipeline.content_quality import ContentQualityError
from test_reader_value_flow import plan_fixture

def msg(value):return SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps(value))],usage=SimpleNamespace(input_tokens=1,output_tokens=2))

class RealPlanDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.value=plan_fixture();self.store={};self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('No network')))
        self.stack.enter_context(patch.object(req,'_plan_context',return_value={'intent':'分析','serp':'{}','requirements':{}}))
        self.stack.enter_context(patch.object(req.anthropic,'Anthropic'))
        self.writer=self.stack.enter_context(patch.object(req,'create_with_retry',return_value=msg(self.value)))
        self.stack.enter_context(patch.object(req,'upsert_artifact',side_effect=self.save))
        self.stack.enter_context(patch.object(review,'upsert_artifact',side_effect=self.save))
        self.stack.enter_context(patch('pipeline.db.get_optional_artifact',side_effect=lambda j,k:self.store.get(k)))
        self.stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=self.save))
        self.stack.enter_context(patch('pipeline.db.get_job',return_value={'tenant_id':'owner'}))
        self.stack.enter_context(patch('pipeline.db.get_user_email',return_value='nishi.aworks@gmail.com'))
        self.patch=self.stack.enter_context(patch.object(req,'_repair_plan_candidate',side_effect=AssertionError('No extra paid plan repair')))

    def save(self,**kw):self.store[kw['step']]=copy.deepcopy(kw);return kw

    def verdict(self,kind):
        result={'valid':True,'issues':[],'claim_checks':{x['id']:{'answer_scope':x['question'],
             'source_requirement':x['source_requirement'],'official_check_required':x.get('official_check_required',True)} for x in self.value['items']},
             'decision_design_checks':{d['id']:{'expected_role':d['role'],'reason':'直接の問いを確認','unnecessary_requirements':[]} for d in self.value['reader_decisions']},
             'decision_reviews':{d['id']:{'status':'pass','reason':'回答を調べる質問がある','missing_requirements':[],'research_ids':[]} for d in self.value['reader_decisions']}}
        if kind=='missing_ids':
            row=next(iter(result['decision_reviews'].values()));row.update(status='fail',missing_requirements=[0],research_ids=[])
        elif kind=='semantic_failure':result.update(valid=False,issues=[{'id':'overall','reason':'回答順序に確認事項'}])
        elif kind=='malformed':return {'valid':True,'issues':[]}
        return result

    def run_plan(self,kind,scoped=True):
        with patch.object(review,'create_with_retry',return_value=msg(self.verdict(kind))) as send:
            result=q.scoped(req.plan,'research_plan')('job','比較') if scoped else req.plan('job','比較')
        return result,send

    def test_observed_empty_research_ids_cannot_fail_completed_plan(self):
        result,send=self.run_plan('missing_ids')
        accepted=json.loads(result['content_text']);self.assertEqual(accepted['items'],self.value['items'])
        self.assertEqual(accepted['reader_decisions'],self.value['reader_decisions'])
        self.assertTrue(result['meta']['ready_for_research']);self.assertFalse(result['meta']['planning_review_passed'])
        self.assertIn('research_plan_review_1_response',self.store)
        self.assertNotIn('research_plan_review_1',self.store) # no invented QA pass
        self.assertTrue(json.loads(self.store['quality_warnings']['content_text'])['warnings'])
        self.writer.assert_called_once();send.assert_called_once();self.patch.assert_not_called()

    def test_semantic_failure_and_malformed_review_do_not_regenerate_plan(self):
        for kind in ('semantic_failure','malformed'):
            with self.subTest(kind=kind):
                self.writer.reset_mock();self.store.clear()
                result,send=self.run_plan(kind)
                self.assertFalse(result['meta']['planning_review_passed'])
                self.writer.assert_called_once();send.assert_called_once();self.patch.assert_not_called()

    def test_current_success_keeps_true_review_result(self):
        result,send=self.run_plan('pass')
        self.assertTrue(result['meta']['planning_review_passed']);self.writer.assert_called_once();send.assert_called_once()

    def test_strict_diagnostics_preserve_rejection_and_paid_response(self):
        with self.assertRaisesRegex(ContentQualityError,'判断の不足に対応する調査'):
            self.run_plan('missing_ids',scoped=False)
        self.assertIn('research_plan_review_1_response',self.store)

    def test_resume_starts_collection_without_sending_plan_or_review_again(self):
        result,_=self.run_plan('missing_ids')
        with patch.object(req,'load_plan',return_value=json.loads(result['content_text'])) as validate:
            stage=research_resume_stage('job','research_plan',['research_plan','fact_sheet','research_completeness'],lambda j,k:self.store.get(k))
        self.assertEqual(stage,'fact_sheet');validate.assert_called_once();self.writer.assert_called_once()
