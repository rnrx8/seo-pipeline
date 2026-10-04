import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import step_research_guard as guard
from pipeline.content_quality import ContentQualityError


class OutlineRepairTests(unittest.TestCase):
    def repair(self, replacement):
        original = '# 構成\n\n無料で全機能を使える\n\n## 比較\n\n表の列は料金・条件。https://example.com'
        artifacts = {'outline': {'content_text': original, 'meta': {}},
                     'fact_sheet': {'content_text': 'facts'}, 'fresh_sources': {'content_text': '[]'}}
        response = SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(
            {'edits': [{'id': 'block-0002', 'new': replacement}]}, ensure_ascii=False))])
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.research_requirements.require_matrix', return_value={}))
            stack.enter_context(patch('pipeline.generation_context.generation_evidence', return_value='初回だけ無料'))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled', return_value=False))
            stack.enter_context(patch('pipeline.ai.create_with_retry', return_value=response))
            stack.enter_context(patch.object(guard, 'source_evidence', return_value='sources'))
            stack.enter_context(patch.object(guard, 'get_artifact', side_effect=lambda _, s: artifacts[s]))
            save = stack.enter_context(patch.object(guard, 'upsert_artifact', side_effect=lambda **kw: kw))
            result = guard.repair_outline('j', '比較', {'checks': [{'status':'fail', 'reason':'無料範囲が誤り'}]})
        return original, result, save

    def test_preserves_other_blocks_and_requires_new_review(self):
        original, result, save = self.repair('初回だけ無料。継続会話は有料')
        self.assertEqual(result['content_text'], original.replace('無料で全機能を使える', '初回だけ無料。継続会話は有料'))
        self.assertFalse(result['meta']['audited'])
        validation = next(c.kwargs for c in save.call_args_list if c.kwargs['step']=='research_validation')
        self.assertFalse(validation['meta']['valid'])

    def test_rejects_whole_outline_inside_one_patch(self):
        with self.assertRaises(ContentQualityError):
            self.repair('# 別の全文構成\n\n## 新しい見出し')
