"""Failures must preserve evidence and never replay previously paid stages."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from contextlib import ExitStack
from pipeline import ai, quality_budget as budget, fresh_sources as fresh
from pipeline.content_quality import ContentQualityError
from pipeline.autofix import classify_error


class PipelineFailureCostTests(unittest.TestCase):
    def test_transient_and_unknown_failures_do_not_restart_completed_steps(self):
        import api_server as api
        for failure in (TimeoutError('timed out'), ValueError('incomplete response')):
            with self.subTest(failure=type(failure).__name__), ExitStack() as stack:
                first=Mock(); failing=Mock(side_effect=failure); last=Mock()
                stack.enter_context(patch.object(api,'get_job',return_value={'delivery_type':'full'}))
                stack.enter_context(patch.object(api,'_resolve_api_key',return_value=None))
                stack.enter_context(patch.object(api,'build_step_plan',return_value=[('serp',first),('fact_sheet',failing),('article',last)]))
                stack.enter_context(patch.object(ai,'validate_model_credentials'))
                stack.enter_context(patch.object(api,'requires_rate_limit_delay',return_value=False))
                status=stack.enter_context(patch.object(api,'update_job_status'))
                stack.enter_context(patch.object(api,'update_job_step'))
                stack.enter_context(patch.object(api,'update_job_error'))
                save=stack.enter_context(patch.object(api,'upsert_artifact'))
                stack.enter_context(patch.object(api,'get_user_email',return_value=''))
                stack.enter_context(patch.object(api,'alert_failed_job'))
                stack.enter_context(patch('pipeline.autofix.create_github_issue'))
                with self.assertRaises(type(failure)):api._run_pipeline('job','keyword')
                first.assert_called_once();failing.assert_called_once();last.assert_not_called()
                self.assertNotIn('queued',[c.args[1] for c in status.call_args_list])
                self.assertEqual(status.call_args.args[1],'failed')
                data=json.loads(save.call_args.kwargs['content_text'])
                self.assertEqual(data['step'],'fact_sheet')
                self.assertEqual(data['detail'],str(failure))
                self.assertFalse(data['automatic_restart'])

    def test_unknown_is_not_classified_as_retryable(self):
        self.assertFalse(classify_error(ValueError('incomplete'))['retryable'])

    def test_auxiliary_models_have_real_cost_accounting(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder}), budget.scope('aux','search_intent'):
            for step in ('query_attrs','intent_chains'):
                model,tokens=ai.get_step_config(step)
                reservation=budget.reserve_claude({'model':model,'max_tokens':tokens,'messages':[]})
                budget.settle(reservation,{'input_tokens':1000,'output_tokens':1000})
            with budget.ledger() as value:
                self.assertEqual(len(value['calls']),2)
                for call in value['calls']:
                    self.assertAlmostEqual(call['cost_usd'],.006)
                    self.assertEqual(call['category'],'generation')

    def test_undefined_model_is_rejected_before_any_generation(self):
        env={'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_BUDGET_DIR':'/unused','OPENAI_API_KEY':'test'}
        with patch.dict(os.environ,env), patch.dict(ai.STEP_CONFIG,{'query_attrs':{'model':'unpriced','max_tokens':800}}):
            with self.assertRaisesRegex(ContentQualityError,'課金開始前'):
                ai.validate_model_credentials({})

    def test_configured_models_pass_preflight(self):
        env={'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_BUDGET_DIR':'/unused','OPENAI_API_KEY':'test'}
        with patch.dict(os.environ,env), patch('pipeline.research_search.require_search_config'):
            ai.validate_model_credentials({})

    def test_truncated_research_keeps_partial_output_without_retry(self):
        response=NS(content=[NS(type='text',text='途中の根拠メモ')],stop_reason='max_tokens',usage=NS(input_tokens=300,output_tokens=6000))
        create=Mock(return_value=response)
        with self.assertRaises(fresh.ResearchResponseError) as caught:
            fresh.run_with_fetch(None,create=create,model='test',max_tokens=6000,system='',prompt='',search_tool={},fresh=fresh.FreshSources({},[]))
        create.assert_called_once()
        self.assertEqual(caught.exception.diagnostic['partial_text'],'途中の根拠メモ')
        self.assertEqual(caught.exception.diagnostic['stop_reason'],'max_tokens')
        self.assertFalse(classify_error(caught.exception)['retryable'])

    def test_incomplete_subject_is_saved_only_as_diagnostic(self):
        from pipeline import research_collection as collection
        response=NS(content=[NS(type='text',text='未完了のメモ')],stop_reason='max_tokens')
        failure=fresh.ResearchResponseError('incomplete_response',response=response)
        sources=Mock(pages={})
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}), \
             patch('pipeline.research_search.require_search_config'), \
             patch('pipeline.research_search.get_optional_artifact',return_value=None), \
             patch.object(collection,'get_optional_artifact',return_value=None), \
             patch.object(collection,'upsert_artifact') as save, \
             patch.object(collection,'run_with_fetch',side_effect=failure):
            with self.assertRaises(fresh.ResearchResponseError):
                collection.collect('job',None,plan={'items':[{'id':'q1','subject':'A'}]},
                    fresh=sources,system='',prompt='',model='test',search_tool={})
        save.assert_called_once()
        record=save.call_args.kwargs
        self.assertEqual(record['step'],'research_collection_1_incomplete')
        self.assertFalse(record['meta']['usable_as_completed_research'])
        self.assertEqual(json.loads(record['content_text'])['partial_text'],'未完了のメモ')
        sources.save.assert_called_once_with('job')

if __name__=='__main__':unittest.main()
