"""A known upstream defect must not trigger paid body-only patch retries."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from pipeline import step_content_audit as audit
from pipeline.content_quality import ContentQualityError


class UpstreamArticleRepairTests(unittest.TestCase):
    def report(self, stage, severity='blocking'):
        return {'valid': False, 'snapshot': 'current-failed-snapshot',
                'checks': [{'key': 'prose_quality', 'status': 'fail', 'reason': '回答範囲の混在',
                            'affected_blocks': [{'id': 'block-0002', 'reason': '回答範囲'}]}],
                'editorial_findings': [{'severity': severity, 'stage_to_fix': stage,
                                       'block_ids': ['block-0002'], 'problem': '回答範囲の混在'}]}

    def test_full_audit_preserves_upstream_failure_without_sending_body_repair(self):
        artifacts = {
            'article': {'content_text': '## 選び方\n\n元の原稿です。', 'meta': {}},
            'outline': {'content_text': '### H2：選び方'},
            'fact_sheet': {'content_text': ''},
            'fresh_sources': {'content_text': json.dumps([
                {'url': 'https://official.example/', 'status': 'success', 'text': '確認済みの原文'}])},
            'content_contract': {'content_text': '{"required_sections":[]}'},
        }
        original = copy.deepcopy(artifacts)
        for stage in ('research', 'outline'):
            with self.subTest(stage=stage), \
                 patch.object(audit, 'get_job', return_value={}), \
                 patch.object(audit, 'get_artifact', side_effect=lambda _, key: artifacts[key]), \
                 patch.object(audit, 'audit', return_value=self.report(stage)), \
                 patch.object(audit, 'upsert_artifact', side_effect=lambda **kw: kw) as save, \
                 patch.object(audit, 'prepare_content_repair') as prepare, \
                 patch.object(audit, 'create_with_retry') as send, \
                 patch.object(audit.anthropic, 'Anthropic'):
                with self.assertRaisesRegex(ContentQualityError, '修正先: ' + stage):
                    audit.run('local', '選び方')
                prepare.assert_not_called()
                send.assert_not_called()
                reports = [c.kwargs for c in save.call_args_list if c.kwargs['step'] == 'content_audit']
                failed = json.loads(reports[-1]['content_text'])
                self.assertFalse(failed['valid'])
                self.assertEqual(failed['editorial_findings'][0]['stage_to_fix'], stage)
                self.assertFalse(any(c.kwargs['step'] == 'article' for c in save.call_args_list))
                self.assertEqual(artifacts, original)

    def test_direct_repair_preparation_stops_before_counter_or_evidence_work(self):
        client = Mock()
        with patch('pipeline.generation_context.generation_evidence') as evidence:
            with self.assertRaisesRegex(ContentQualityError, '修正先: outline'):
                audit.prepare_content_repair(client, job_id='local', text='元の原稿',
                    report=self.report('outline'), requirements={}, sources=[], facts='', contract={}, issues=[])
        evidence.assert_not_called()
        client.with_options.assert_not_called()

    def test_saved_patch_cannot_bypass_upstream_routing(self):
        with patch.object(audit, 'apply_block_edits') as apply:
            with self.assertRaisesRegex(ContentQualityError, '修正先: research'):
                audit.apply_reviewed_edits('元の原稿', '{"edits":[]}', None,
                    self.report('research'), [], '', '', None, {}, None)
        apply.assert_not_called()

    def test_optional_upstream_suggestion_does_not_block_article_or_presentation_repairs(self):
        report = self.report('outline', severity='improvement')
        report['editorial_findings'].extend([
            {'severity': 'blocking', 'stage_to_fix': stage} for stage in ('article', 'presentation')])
        audit.require_article_repair_scope(report)


if __name__ == '__main__':
    unittest.main()
