"""Offline regressions for the 2026-10-13 saved outline-repair failure."""
import copy
import json
import unittest

from pipeline.content_edits import REPAIR_OUTPUT_CONFIG, scoped_repair_output_config
from pipeline.content_quality import ContentQualityError
from pipeline.review_scope import validate_edits


class OutlineRepairScopeRegression(unittest.TestCase):
    def test_valid_json_with_outside_edit_keeps_scope_error(self):
        # The actual failed job changed adjacent 0044 and 0108 in addition to
        # the six reviewer-authorized targets. JSON itself was well formed.
        targets = ['block-0014', 'block-0042', 'block-0052', 'block-0092', 'block-0104', 'block-0106']
        raw = json.dumps({'edits': [{'id': value, 'new': 'fixture replacement'}
                                  for value in targets + ['block-0044', 'block-0108']]})
        with self.assertRaisesRegex(ContentQualityError, '^指摘対象外の段落'):
            validate_edits(raw, {'target_block_ids': targets})

    def test_bad_json_still_has_format_error(self):
        with self.assertRaisesRegex(ContentQualityError, '^局所修正の形式が不正'):
            validate_edits('{broken', {'target_block_ids': ['block-0014']})

    def test_authorized_edits_pass(self):
        validate_edits(json.dumps({'edits': [{'id': 'block-0014', 'new': 'replacement'}]}),
                       {'target_block_ids': ['block-0014']})

    def test_generation_schema_excludes_neighboring_context_without_mutating_global(self):
        before = copy.deepcopy(REPAIR_OUTPUT_CONFIG)
        schema = scoped_repair_output_config(['block-0014', 'block-0042'])
        ids = schema['format']['schema']['properties']['edits']['items']['properties']['id']['enum']
        self.assertEqual(ids, ['block-0014', 'block-0042'])
        self.assertNotIn('block-0044', ids)
        self.assertEqual(REPAIR_OUTPUT_CONFIG, before)

    def test_missing_targets_stop_before_generation(self):
        with self.assertRaises(ContentQualityError):
            scoped_repair_output_config([])


if __name__ == '__main__':
    unittest.main()
