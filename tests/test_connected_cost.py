import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evaluations'/'tiered-facts'))
from measure_connected_flow import cost


class ConnectedCostTests(unittest.TestCase):
    def test_margin_can_cross_long_context_rate_boundary(self):
        self.assertAlmostEqual(cost(272000,6000,'gpt-6.1-sol',margin=1),.74)
        self.assertAlmostEqual(cost(272001,6000,'gpt-6.1-sol',margin=1),1.450005)
        self.assertAlmostEqual(cost(226667,6000,'gpt-6.1-sol'),1.450005)

    def test_input_only_does_not_silently_include_output_or_margin(self):
        self.assertAlmostEqual(cost(100000,0,'gpt-6.1-sol',margin=1),.25)
        self.assertAlmostEqual(cost(100000,6000,'gpt-6.1-sol'),.36)

    def test_lower_model_comparison_changes_price_not_token_assumptions(self):
        self.assertAlmostEqual(cost(100000,6000,'gpt-6-luna'),.018)
        with self.assertRaises(KeyError):cost(100000,6000,'unknown')
