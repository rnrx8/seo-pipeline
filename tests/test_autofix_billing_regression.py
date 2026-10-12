import unittest

from pipeline.autofix import classify_error
from pipeline.content_quality import ContentQualityError


class BillingClassificationRegression(unittest.TestCase):
    def test_saved_job_billing_error_keeps_billing_diagnosis(self):
        result = classify_error(ContentQualityError('OpenAI APIの利用残高がありません。残高追加後に確認工程から再開してください。'))
        self.assertEqual(result['reason'], 'APIクレジット不足')
        self.assertFalse(result['retryable'])

    def test_wrapped_insufficient_quota_is_not_a_quality_failure(self):
        result = classify_error(ContentQualityError('429 insufficient_quota'))
        self.assertEqual(result['reason'], 'APIクレジット不足')
        self.assertFalse(result['retryable'])

    def test_genuine_content_failure_stays_content_failure(self):
        result = classify_error(ContentQualityError('必要情報が未充足です'))
        self.assertEqual(result['reason'], '記事の根拠・比較内容が品質基準を満たしていません')

    def test_rate_limit_remains_distinct_from_quota(self):
        result = classify_error(RuntimeError('429 rate_limit_exceeded'))
        self.assertEqual(result['reason'], 'レートリミット')
        self.assertTrue(result['retryable'])
