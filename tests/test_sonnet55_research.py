import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock, patch
from pipeline import ai, quality_budget as budget, research_collection, step_fact_sheet


class Sonnet55ResearchTests(unittest.TestCase):
    def test_only_research_model_changes_and_imported_collector_uses_it(self):
        self.assertEqual(ai.get_step_config('fact_sheet'), ('claude-sonnet-5-5', 39000))
        self.assertEqual(step_fact_sheet.MODEL, 'claude-sonnet-5-5')
        for step in ('query_attrs', 'intent_chains'):
            self.assertEqual(ai.get_step_config(step)[0], 'claude-haiku-4-5-20251001')
        for step in ('fact_review', 'service_map'):
            self.assertEqual(ai.get_step_config(step)[0], 'claude-sonnet-4-6')

    def test_research_effort_preserves_format_without_opus_output_addition(self):
        client = MagicMock()
        with patch.object(ai, 'tiered_review_enabled', return_value=False):
            ai.create_with_retry(client, model='claude-sonnet-5-5', max_tokens=9000,
                                 messages=[], output_config={'format': {'type': 'json_schema'}})
        request = client.messages.stream.call_args.kwargs
        self.assertEqual(request['max_tokens'], 9000)
        self.assertEqual(request['output_config'], {'effort': 'medium', 'format': {'type': 'json_schema'}})

    def test_actual_research_collector_uses_same_output_cost_ceiling(self):
        for model, cap in [('claude-sonnet-4-6', 6000), ('claude-sonnet-5-5', 9000)]:
            with patch.object(research_collection, 'tiered_review_enabled', return_value=True), \
                 patch('pipeline.research_search.require_search_config'), \
                 patch('pipeline.research_search.SourceSearch', return_value=Mock()), \
                 patch.object(research_collection, 'get_optional_artifact', return_value=None), \
                 patch.object(research_collection, 'upsert_artifact', return_value={}), \
                 patch.object(research_collection, 'run_with_fetch', return_value=(NS(stop_reason='end_turn',
                     usage=NS(input_tokens=100, output_tokens=100)), 'answer', [], [])) as run:
                research_collection.collect('local', Mock(), plan={'items': [{'id': 'q1', 'subject': 'one'}]},
                    fresh=Mock(pages={}), system='system', prompt='prompt', model=model, search_tool={})
                self.assertEqual(run.call_args.kwargs['max_tokens'], cap)
        self.assertEqual(6000*15, 9000*10)

    def test_real_ledger_settles_cache_and_output_at_sonnet55_prices(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'QUALITY_BUDGET_DIR': directory}), \
             budget.scope('sonnet55-test', 'fact_sheet'):
            index = budget.reserve_claude({'model': 'claude-sonnet-5-5', 'max_tokens': 9000, 'messages': []})
            budget.settle(index, {'input_tokens': 1000, 'output_tokens': 500,
                'cache_creation_input_tokens': 3000, 'cache_creation': {
                    'ephemeral_5m_input_tokens': 2000, 'ephemeral_1h_input_tokens': 1000},
                'cache_read_input_tokens': 10000})
            with budget.ledger() as value:
                self.assertAlmostEqual(value['calls'][index]['cost_usd'], .017)
                self.assertEqual(value['calls'][index]['category'], 'generation')
