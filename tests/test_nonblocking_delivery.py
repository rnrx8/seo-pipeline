"""Delivery must not depend on perfect QA; strict truth and spending stay intact."""
import copy,json,unittest
from contextlib import ExitStack
from unittest.mock import patch
from pipeline import quality_warnings as q,content_quality as cq
from pipeline.editorial_recovery import EditorialRecoveryError,UpstreamRepairRequired
from pipeline.draft_readiness import DraftReadinessError

class NonblockingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.store={'article':{'content_text':'# 題\n\n## 答え\n\n保存済み本文。','meta':{'content_audited':True,'content_audit_snapshot':'old'}}}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('pipeline.db.get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(self.store.get(s))))
        self.stack.enter_context(patch('pipeline.db.get_artifact',side_effect=lambda j,s:copy.deepcopy(self.store[s])))
        def save(**kw):self.store[kw['step']]=copy.deepcopy(kw);return kw
        self.stack.enter_context(patch('pipeline.db.upsert_artifact',side_effect=save))
        self.stack.enter_context(patch('pipeline.db.get_job',return_value={'tenant_id':'owner'}))
        self.stack.enter_context(patch('pipeline.db.get_user_email',return_value='nishi.aworks@gmail.com'))

    def test_every_post_body_stage_delivers_saved_draft_on_quality_or_format_error(self):
        original=self.store['article']['content_text']
        for stage in sorted(q.POST_DRAFT_STAGES):
            for error in (cq.ContentQualityError('QA不合格'),EditorialRecoveryError('修正回数上限'),
                          ContentError('insufficient_quota'),ValueError('invalid JSON'),AttributeError('invalid reference'),ConnectionError('provider unavailable')):
                with self.subTest(stage=stage,error=type(error).__name__):
                    def fail(*a,**k):raise error
                    q.scoped(fail,stage)('job')
                    self.assertEqual(self.store['article']['content_text'],original)
                    self.assertFalse(self.store['article']['meta']['content_audited'])
                    self.assertIsNone(self.store['article']['meta']['content_audit_snapshot'])
        self.assertTrue(json.loads(self.store['quality_warnings']['content_text'])['warnings'])
        self.assertFalse(q.enabled())

    def test_completed_body_survives_optional_lead_finishing_failure(self):
        def finish(*a):
            q.retain_generated_draft({'content_text':'# 新しい完成初稿\n\n## 答え\n\n本文。','meta':{'parts':3}})
            raise cq.ContentQualityError('リードの長さ指定を満たせません')
        q.scoped(finish,'article')('job')
        self.assertIn('新しい完成初稿',self.store['article']['content_text'])
        self.assertTrue(self.store['article']['meta']['completion_with_warnings'])

    def test_previous_body_never_masks_new_writer_failure_before_checkpoint(self):
        old=copy.deepcopy(self.store['article'])
        def fail(*a):raise cq.ContentQualityError('本文生成の利用残高不足')
        with self.assertRaises(cq.ContentQualityError):q.scoped(fail,'article')('job')
        self.assertEqual(self.store['article'],old)

    def test_prebody_checks_keep_quality_findings_and_continue(self):
        for stage in sorted(q.SOFT_STAGES-q.POST_DRAFT_STAGES):
            def fail(*a):raise cq.ContentQualityError('根拠参照の再判定に確認事項')
            self.assertIsNone(q.scoped(fail,stage)('job'))
        self.assertNotIn('research_matrix',self.store) # no fabricated evidence/pass

    def test_no_body_cannot_mask_missing_service_or_missing_inputs(self):
        self.store.pop('article')
        for stage in q.SOFT_STAGES:
            def fail(*a):raise cq.ContentQualityError('insufficient_quota')
            with self.assertRaises(cq.ContentQualityError):q.scoped(fail,stage)('job')
        def missing(*a):raise DraftReadinessError('missing input')
        with self.assertRaises(DraftReadinessError):q.scoped(missing,'research_validation')('job')

    def test_busy_worker_cannot_be_relabelled_completed_by_second_worker(self):
        def busy(*a):raise cq.GenerationBusyError('owned by another worker')
        original=copy.deepcopy(self.store)
        with self.assertRaises(cq.GenerationBusyError):q.scoped(busy,'content_audit')('job')
        self.assertEqual(original,self.store)

    def test_save_failure_is_not_claimed_as_delivery(self):
        def fail(*a):raise cq.ContentQualityError('QA不合格')
        with patch('pipeline.db.upsert_artifact',side_effect=ConnectionError('store unavailable')):
            with self.assertRaises(ConnectionError):q.scoped(fail,'content_audit')('job')

    def test_notification_failure_does_not_lose_warning_or_stop(self):
        with patch('pipeline.db.get_user_email',side_effect=RuntimeError('mail unavailable')):
            q.scoped(lambda *a:q.warn('確認事項'),'research_validation')('job')
        self.assertTrue(json.loads(self.store['quality_warnings']['content_text'])['warnings'])

    def test_failed_final_cannot_resolve_warnings_or_seal_publication(self):
        self.store['structure_validation_final']={'content_text':'{"valid":false,"violations":[{"key":"coverage"}]}'}
        with self.assertRaises(cq.ContentQualityError):q.resolve_after_final('job')

ContentError=cq.ContentQualityError

class ProductionControllerDeliveryTests(unittest.TestCase):
    def test_api_and_cli_reach_done_with_failed_qa_and_without_model_calls(self):
        import os,socket,importlib
        from pipeline import runner,step_plan
        api=importlib.import_module('api_server')
        for controller in ('api','cli'):
            with self.subTest(controller=controller),ExitStack() as stack:
                store={};statuses=[];job={'id':'fixture-job','main_keyword':'例','delivery_type':'full'}
                stack.enter_context(patch.dict(os.environ,{'ARTICLE_REVISION_TRACKING':'0'}))
                stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')))
                stack.enter_context(patch.object(step_plan,'tiered_review_enabled',return_value=False))
                def save(**kw):store[kw['step']]=copy.deepcopy(kw);return kw
                def read(j,key):
                    if key not in store:raise ValueError('Artifact not found: '+key)
                    return copy.deepcopy(store[key])
                for module in (q,runner,api,step_plan.step_final_validate):
                    for name,fn in [('get_job',lambda *a:job),('get_artifact',read),
                                    ('get_optional_artifact',lambda j,k:copy.deepcopy(store.get(k))),
                                    ('upsert_artifact',save),('update_job_status',lambda j,s:statuses.append(s)),
                                    ('update_job_step',lambda *a:None)]:
                        if hasattr(module,name):stack.enter_context(patch.object(module,name,side_effect=fn))
                for name,fn in [('get_optional_artifact',lambda j,k:copy.deepcopy(store.get(k))),('get_artifact',read),
                                ('upsert_artifact',save),('get_job',lambda *a:job),('get_user_email',lambda *a:'nishi.aworks@gmail.com')]:
                    stack.enter_context(patch('pipeline.db.'+name,side_effect=fn))
                for module in (runner,):stack.enter_context(patch.object(module,'validate_model_credentials'))
                stack.enter_context(patch('pipeline.ai.validate_model_credentials'))
                stack.enter_context(patch.object(api,'_resolve_api_key',return_value=None))
                stack.enter_context(patch.object(api.time,'sleep'))
                def generated(key):
                    def run(j,k,**kw):
                        text='# 題\n\n## 答え\n\n生成された本文。' if key=='article' else ('[]' if key=='fresh_sources' else '{}')
                        return save(job_id=j,step=key,content_text=text,meta={},content_type='text/markdown')
                    return run
                for module,key in [(step_plan.step_serp,'serp'),(step_plan.step_intent,'search_intent'),
                    (step_plan.step_fact_sheet,'fact_sheet'),(step_plan.step_reference,'reference_structure'),
                    (step_plan.step_content_contract,'content_contract'),(step_plan.step_outline,'outline'),
                    (step_plan.step_article,'article')]:stack.enter_context(patch.object(module,'run',side_effect=generated(key)))
                stack.enter_context(patch.object(step_plan.research_requirements,'plan',side_effect=generated('research_plan')))
                def quality_failure(*a,**kw):raise cq.ContentQualityError('根拠・構成の確認事項が残る')
                stack.enter_context(patch.object(step_plan.research_requirements,'verify',side_effect=quality_failure))
                stack.enter_context(patch.object(step_plan.step_structure_guard,'run_before_research',side_effect=quality_failure))
                stack.enter_context(patch.object(step_plan.step_research_guard,'run',side_effect=quality_failure))
                stack.enter_context(patch('pipeline.editorial_recovery.run',side_effect=EditorialRecoveryError('上流修正上限')))
                stack.enter_context(patch('pipeline.article_revisions.seal',side_effect=AssertionError('must not seal failed QA')))
                if controller=='api':api._run_pipeline('fixture-job','例')
                else:runner.run_pipeline('例',job_id='fixture-job')
                self.assertEqual(statuses[-1],'done');self.assertNotIn('failed',statuses)
                self.assertEqual(store['article']['content_text'],'# 題\n\n## 答え\n\n生成された本文。')
                self.assertFalse(json.loads(store['structure_validation_final']['content_text'])['valid'])
                self.assertFalse(store['article']['meta']['content_audited'])
                self.assertTrue(json.loads(store['quality_warnings']['content_text'])['warnings'])
