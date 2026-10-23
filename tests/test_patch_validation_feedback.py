import json
import unittest
from pipeline.content_edits import source_issue_locations, apply_block_edits, content_blocks
from pipeline.content_quality import ContentPatchValidationError
from pipeline.step_content_audit import apply_reviewed_edits
from pipeline.section_identity import bind_sections


class PatchValidationFeedbackTests(unittest.TestCase):
    def test_inserted_paragraphs_are_reported_using_original_source_id(self):
        before = '# 題\n\n元の説明。\n\n後の説明。'
        edits = [{'id': 'block-0002', 'new': '変更した説明。\n\n追加した説明。'}]
        after = apply_block_edits(before, json.dumps({'edits': edits}))
        issue = {'key': 'continuous_prose', 'affected_blocks': [{'id': 'block-0004', 'reason': '長い'}, {'id': 'block-0006', 'reason': '長い'}]}
        result = source_issue_locations(before, after, edits, [issue])[0]
        self.assertEqual([b['id'] for b in result['affected_blocks']], ['block-0002', 'block-0004'])
        self.assertEqual(result['changed_source_block_ids'], ['block-0002'])
        self.assertEqual(result['location_space'], 'original_article_blocks')
        self.assertEqual(issue['affected_blocks'][0]['id'], 'block-0004')

    def test_removed_paragraph_does_not_reassign_following_issue_to_deleted_id(self):
        before = '# 題\n\n削除する。\n\n残す説明。'
        edits = [{'id': 'block-0002', 'new': ''}]
        after = apply_block_edits(before, json.dumps({'edits': edits}))
        target = next(b['id'] for b in content_blocks(after) if '残す説明' in b['text'])
        result = source_issue_locations(before, after, edits, [{'affected_blocks': [{'id': target, 'reason': '確認'}]}])[0]
        self.assertEqual(result['affected_blocks'][0]['id'], 'block-0004')

    def test_missing_fix_and_new_mechanical_issue_are_returned_together(self):
        before = '# 題\n\n## 比較\n\n短い説明。\n\n## 次\n\n残す説明。'
        outline = '### H2：比較\n\n### H2：次'
        report = {'checks': [{'key': 'prose_quality', 'status': 'fail', 'affected_blocks': [{'id': 'block-0008'}]}],
                  'editorial_findings': [{'severity': 'blocking', 'stage_to_fix': 'article', 'block_ids': ['block-0008']}]}
        raw = json.dumps({'edits': [{'id': 'block-0004', 'new': 'あ' * 310}]})
        with self.assertRaises(ContentPatchValidationError) as caught:
            apply_reviewed_edits(before, raw, None, report, [], '', outline, None, {}, bind_sections(before, outline))
        message = str(caught.exception)
        self.assertIn('修正がありません', message)
        self.assertIn('block-0008', message)
        self.assertIn('continuous_prose', message)
        self.assertIn('310', message)
        self.assertIn('original_article_blocks', message)

    def test_unknown_candidate_provenance_does_not_guess_an_original_id(self):
        with self.assertRaises(ValueError):source_issue_locations('# 題', '# 別の題', [], [])


if __name__ == '__main__':unittest.main()
