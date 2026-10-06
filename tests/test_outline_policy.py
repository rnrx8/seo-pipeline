import unittest
from unittest.mock import patch
from pipeline.outline_policy import current_policy, require_current_outline
from pipeline.content_quality import ContentQualityError


class OutlinePolicyTests(unittest.TestCase):
    def test_missing_and_old_policy_are_not_silently_certified(self):
        for meta in ({}, {'editorial_policy':'old'}):
            artifact={'content_text':'旧構成を保持','meta':meta}
            with self.assertRaises(ContentQualityError): require_current_outline(artifact)
            self.assertEqual(artifact['content_text'],'旧構成を保持')
            self.assertEqual(artifact['meta'],meta)
        require_current_outline({'meta':{'editorial_policy':current_policy()}})

    def test_changed_editorial_policy_invalidates_outline(self):
        artifact={'meta':{'editorial_policy':current_policy()}}
        with patch('pipeline.outline_policy.READER_PRESENTATION_POLICY','changed'):
            with self.assertRaises(ContentQualityError): require_current_outline(artifact)

    def test_stale_outline_stops_before_audit_repair_or_artifact_write(self):
        from pipeline import step_research_guard as guard
        for method,args in ((guard.run,('job','比較')),(guard.repair_outline,('job','比較',{'checks':[]}))):
            with self.subTest(method=method.__name__), \
                 patch.object(guard,'get_artifact',return_value={'content_text':'旧構成'}), \
                 patch.object(guard,'upsert_artifact') as save, \
                 patch.object(guard,'audit') as audit, \
                 patch.object(guard.anthropic,'Anthropic') as client, \
                 patch('pipeline.research_requirements.require_matrix') as matrix:
                with self.assertRaises(ContentQualityError): method(*args)
                save.assert_not_called();audit.assert_not_called();client.assert_not_called();matrix.assert_not_called()
