import unittest

from pipeline.step_plan import build_step_plan


class StepPlanTests(unittest.TestCase):
    def test_full_plan_contains_service_and_validation_steps(self):
        keys = [key for key, _ in build_step_plan({
            "delivery_type": "full",
            "service_id": "service-id",
            "cta_id": "cta-id",
            "high_accuracy_mode": True,
        })]
        self.assertLess(keys.index("content_contract"), keys.index("outline"))
        self.assertLess(keys.index("structure_guard"), keys.index("service_map"))
        self.assertNotIn("review", keys)
        self.assertLess(keys.index("fact_review"), keys.index("content_audit"))
        self.assertEqual(keys[-1], "final_structure_validation")

    def test_outline_only_still_runs_structure_guard(self):
        keys = [key for key, _ in build_step_plan({"delivery_type": "outline_only"})]
        self.assertEqual(keys[-1], "research_validation")
        self.assertNotIn("article", keys)

    def test_research_only_stops_before_contract(self):
        keys = [key for key, _ in build_step_plan({"delivery_type": "research_only"})]
        self.assertEqual(keys, ["serp", "search_intent", "research_plan", "fact_sheet", "research_completeness"])


if __name__ == "__main__":
    unittest.main()
