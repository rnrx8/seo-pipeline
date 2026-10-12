import unittest
from unittest.mock import patch
from pipeline.content_quality import ContentQualityError
from pipeline.quality_warnings import scoped, enabled

class WarningPolicyTest(unittest.TestCase):
    def test_quality_failure_continues_without_leaking_context(self):
        def fail(*args, **kwargs):
            self.assertTrue(enabled())
            raise ContentQualityError('必須質問の調査が未完了です')
        with patch('pipeline.quality_warnings.warn') as warning:
            self.assertIsNone(scoped(fail, 'research_completeness')('job'))
            warning.assert_called_once()
        self.assertFalse(enabled())

    def test_billing_and_generation_failures_still_fail(self):
        for stage, message in [('outline', 'OpenAI APIの利用残高がありません'),
                               ('article', '本文生成が未完成です')]:
            def fail(*args, **kwargs): raise ContentQualityError(message)
            with self.assertRaises(ContentQualityError), patch('pipeline.quality_warnings.warn') as warning:
                scoped(fail, stage)('job')
            warning.assert_not_called()
            self.assertFalse(enabled())

    def test_unavailable_verification_records_unperformed_check(self):
        def fail(*args, **kwargs): raise ContentQualityError('OpenAI APIの利用残高がありません')
        with patch('pipeline.quality_warnings.warn') as warning:
            scoped(fail, 'research_completeness')('job')
            self.assertIn('確認未完了', warning.call_args.args[0])

    def test_pending_research_without_items_does_not_crash_or_invent_answers(self):
        from pipeline.generation_context import decision_bundle
        with patch('pipeline.quality_warnings.warn') as warning:
            result = scoped(lambda *args: decision_bundle(
                {'items': [{'id': 'q01', 'question': 'Question'}]},
                {'valid': False, 'status': 'running'}, quotes=False), 'outline')('job')
        self.assertIn('"decisions": []', result)
        self.assertTrue(warning.called)

    def test_non_quality_exception_is_not_hidden(self):
        def fail(*args, **kwargs): raise ConnectionError('network')
        with self.assertRaises(ConnectionError): scoped(fail, 'content_audit')('job')

    def test_warning_routing_and_deduplication(self):
        from pipeline.quality_warnings import warn
        for email, expected in [('nishi.aworks@gmail.com', 0), ('other@example.com', 1)]:
            with patch('pipeline.db.get_optional_artifact', return_value=None), \
                 patch('pipeline.db.upsert_artifact') as save, \
                 patch('pipeline.db.get_job', return_value={'tenant_id': 'owner'}), \
                 patch('pipeline.db.get_user_email', return_value=email), \
                 patch('pipeline.notify.alert_quality_warning') as discord:
                scoped(lambda *args: warn('調査不足'), 'research_validation')('job')
                self.assertEqual(discord.call_count, expected)
                self.assertIn('warn_and_continue', save.call_args.kwargs['content_text'])

if __name__ == '__main__': unittest.main()
