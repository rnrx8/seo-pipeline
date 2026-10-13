"""Production resumes past trial caps without losing request history."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pipeline import quality_budget as budget
from pipeline.content_quality import ContentQualityError

class ProductionSpendingModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'QUALITY_BUDGET_DIR':self.tmp.name},clear=False)
        self.env.start()
        self.mode=patch.dict(os.environ)
        self.mode.start()
        os.environ.pop('QUALITY_BUDGET_MODE',None)
        self.scope=budget.scope('existing-job','content_audit');self.scope.__enter__()
        with budget.ledger() as value:
            value.update(total_limit_usd=.01,category_limits_usd={'generation':.01,'quality':.01})
            value['calls'].append({'reserved_usd':2,'cost_usd':2,'status':'accounted','category':'quality','stage':'fact_sheet'})
    def tearDown(self):
        self.scope.__exit__(None,None,None);self.mode.stop();self.env.stop();self.tmp.cleanup()
    def test_default_production_ignores_all_trial_caps_but_preserves_history(self):
        os.environ['QUALITY_TOTAL_LIMIT_USD']='.01'
        os.environ['QUALITY_COMPLETION_EVAL']='1'
        self.assertFalse(budget.spending_limits_enabled())
        for reserve in (
            lambda:budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':150000}),
            lambda:budget.reserve_claude({'model':'claude-opus-5-5','max_tokens':150000,'messages':[]}),
            lambda:budget.reserve_search('既婚者クラブ カドル'),
        ):
            with budget.scope('existing-job','fact_sheet'):
                index=reserve()
            self.assertGreater(index,0)
        with budget.ledger() as value:
            self.assertEqual(value['calls'][0]['cost_usd'],2)
            self.assertEqual(len(value['calls']),4)
            self.assertEqual(value['total_limit_usd'],.01)
    def test_production_review_can_exceed_phase_cap_but_rejects_duplicate(self):
        request={'messages':[{'content':'{}'}]}
        with budget.request_scope('existing-job','tiered_article_coverage','new',request):
            first=budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':150000})
            budget.settle(first,{'input_tokens':1,'output_tokens':1})
            with self.assertRaisesRegex(ContentQualityError,'同じ確認要求'):
                budget.reserve({'model':'gpt-6.1-sol','max_output_tokens':150000})
    def test_explicit_bounded_trial_still_stops_on_saved_total_cap(self):
        with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'bounded'}):
            with self.assertRaisesRegex(ContentQualityError,'テスト全体'):
                budget.reserve_search('query')
    def test_invalid_mode_fails_before_paid_request(self):
        with patch.dict(os.environ,{'QUALITY_BUDGET_MODE':'typo'}):
            with self.assertRaisesRegex(ContentQualityError,'QUALITY_BUDGET_MODE'):
                budget.reserve_search('query')
