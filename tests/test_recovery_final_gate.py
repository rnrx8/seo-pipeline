"""Recovered jobs cannot turn a failed/missing final check into a warning/done."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from pipeline import step_final_validate as final, content_quality as cq, editorial_recovery as recovery
from pipeline.quality_warnings import scoped


class RecoveryFinalGateTests(unittest.TestCase):
    def run_final(self, status='completed', *, matrix_error=False, audit_error=False, snapshot='current'):
        records = {'article': {'content_text': '# 題\n\n## 答え\n\n本文。', 'meta': {}},
                   'outline': {'content_text': '構成'}, 'content_contract': {'content_text': '{}'},
                   'fact_sheet': {'content_text': ''}, 'fresh_sources': {'content_text': '[{"url":"https://official.example/","status":"success","text":"公式取得本文"}]'},
                   'content_audit': {'content_text': '{}'}}
        record = {'content_text': json.dumps({'status': status, 'final_snapshot': snapshot})} if status else None
        with ExitStack() as stack:
            for key in ('readability_issues', 'validate_structure', 'cta_placement_issues', 'validate_delivery', 'scope_issues'):
                stack.enter_context(patch.object(final, key, return_value=[]))
            stack.enter_context(patch.object(final, 'get_artifact', side_effect=lambda j, s: copy.deepcopy(records[s])))
            stack.enter_context(patch.object(final, 'get_optional_artifact', return_value=record))
            stack.enter_context(patch.object(final, 'get_job', return_value={}))
            stack.enter_context(patch.object(final, 'final_review_requirements', return_value={}))
            stack.enter_context(patch.object(final, 'snapshot', return_value='current'))
            stack.enter_context(patch.object(final, 'upsert_artifact', side_effect=lambda **kw: records.update({kw['step']: kw}) or kw))
            strict_matrix = stack.enter_context(patch('pipeline.research_requirements._require_matrix_strict',
                side_effect=cq.ContentQualityError('資料が変わった') if matrix_error else None, return_value={}))
            strict_audit = stack.enter_context(patch('pipeline.content_quality._require_audit_strict',
                side_effect=cq.ContentQualityError('QAが未合格') if audit_error else None))
            legacy_matrix = stack.enter_context(patch('pipeline.research_requirements.require_matrix', return_value={}))
            legacy_audit = stack.enter_context(patch.object(cq, 'require_audit'))
            warn = stack.enter_context(patch('pipeline.quality_warnings.warn'))
            seal = stack.enter_context(patch('pipeline.article_revisions.seal'))
            expected_failure = (status is not None and status != 'completed') or matrix_error or audit_error or snapshot != 'current'
            if expected_failure:
                with self.assertRaises(recovery.EditorialRecoveryError if status else cq.ContentQualityError):scoped(final.run, 'final_structure_validation')('job', 'kw')
                seal.assert_not_called()
                self.assertFalse(json.loads(records['structure_validation_final']['content_text'])['valid'])
            else:
                result = scoped(final.run, 'final_structure_validation')('job', 'kw')
                self.assertTrue(json.loads(result['content_text'])['valid']);seal.assert_called_once()
            warn.assert_not_called();legacy_matrix.assert_not_called();legacy_audit.assert_not_called()
            strict_matrix.assert_called_once();strict_audit.assert_called_once()

    def test_completed_current_recovery_requires_strict_matrix_and_full_audit(self):self.run_final()
    def test_pending_recovery_cannot_seal_or_continue_as_warning(self):self.run_final('running')
    def test_failed_recovery_cannot_seal_or_continue_as_warning(self):self.run_final('failed')
    def test_changed_sources_block_completion(self):self.run_final(matrix_error=True)
    def test_failed_audit_blocks_completion(self):self.run_final(audit_error=True)
    def test_old_recovery_snapshot_blocks_completion(self):self.run_final(snapshot='old')
    def test_normal_job_without_revision_or_recovery_uses_same_strict_checks(self):self.run_final(status=None)
    def test_normal_job_cannot_warn_past_failed_research(self):self.run_final(status=None,matrix_error=True)
    def test_normal_job_cannot_warn_past_failed_article_qa(self):self.run_final(status=None,audit_error=True)


if __name__ == '__main__':unittest.main()
