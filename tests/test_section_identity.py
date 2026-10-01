import copy
import unittest
from pipeline.article_quality import validate_delivery
from pipeline.section_identity import bind_sections, carry_sections, valid_binding, unchanged_heading_binding

OUTLINE='### H2：比較\n#### H3：気軽さ・会員数で選ぶならサービスA\n#### H3：料金で選ぶならサービスB\n### H2：まとめ'
TEXT='## 比較\n比較します。\n### 気軽さ・会員数で選ぶならサービスA\n'+'説明。'*25+'\n### 料金で選ぶならサービスB\n'+'比較条件。'*25+'\n## まとめ\nまとめます。'

class SectionIdentityTests(unittest.TestCase):
    def test_corrected_heading_keeps_id_without_weakening_similarity(self):
        binding=bind_sections(TEXT,OUTLINE)
        revised=TEXT.replace('気軽さ・会員数で選ぶならサービスA','機能と費用を確かめたい人にはサービスA')
        self.assertTrue(validate_delivery(revised,OUTLINE))
        updated=carry_sections(TEXT,revised,OUTLINE,binding)
        self.assertEqual(binding['entries'][1]['id'],updated['entries'][1]['id'])
        self.assertEqual(validate_delivery(revised,OUTLINE,section_map=updated),[])
        self.assertNotIn('section-',revised)

    def test_missing_level_and_added_sections_are_not_title_edits(self):
        binding=bind_sections(TEXT,OUTLINE)
        for revised in [TEXT.replace('### 気軽さ・会員数で選ぶならサービスA\n',''),TEXT.replace('### 料金で','## 料金で'),TEXT+'\n## 追加\n本文']:
            with self.assertRaises(ValueError):carry_sections(TEXT,revised,OUTLINE,binding)

    def test_changed_outline_or_untracked_heading_cannot_reuse_ids(self):
        binding=bind_sections(TEXT,OUTLINE)
        self.assertFalse(valid_binding(TEXT,OUTLINE+'変更',binding))
        self.assertFalse(valid_binding(TEXT.replace('会員数','登録人数'),OUTLINE,binding))
        self.assertTrue(validate_delivery(TEXT,OUTLINE+'変更',section_map=binding))

    def test_duplicate_ids_and_empty_body_rejected(self):
        binding=bind_sections(TEXT,OUTLINE)
        bad=copy.deepcopy(binding);bad['entries'][1]['id']=bad['entries'][0]['id']
        self.assertFalse(valid_binding(TEXT,OUTLINE,bad))
        revised=TEXT.replace('説明。'*25,'')
        updated=carry_sections(TEXT,revised,OUTLINE,binding)
        self.assertIn('empty_section',[x['key'] for x in validate_delivery(revised,OUTLINE,section_map=updated)])

    def test_full_rewrite_does_not_guess_identity_by_position(self):
        binding=bind_sections(TEXT,OUTLINE)
        self.assertIs(unchanged_heading_binding(TEXT,TEXT+'追記',binding),binding)
        self.assertIsNone(unchanged_heading_binding(TEXT,TEXT.replace('会員数','特徴'),binding))
