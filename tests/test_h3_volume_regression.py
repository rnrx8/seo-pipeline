import unittest
from pipeline.readability import h3_volume_regressions
class H3VolumeRegressionTests(unittest.TestCase):
    def test_removing_disclaimer_cannot_leave_new_thin_chapter(self):
        old='### 登録と利用\n\n'+'あ'*170+'\n\n'+'い'*40
        new='### 登録と利用\n\n'+'あ'*170
        self.assertEqual(h3_volume_regressions(old,new)[0]['characters'],170)
    def test_table_contents_count_without_markdown_padding(self):
        table='\n\n| 項目 | 内容 |\n|---|---|\n| 機能 | '+'い'*70+' |'
        self.assertEqual(h3_volume_regressions('### 商品\n\n'+'あ'*220,'### 商品\n\n'+'あ'*140+table),[])
        self.assertTrue(h3_volume_regressions('### 商品\n\n'+'あ'*220,'### 商品\n\n'+'あ'*100+table))
    def test_h4_exception_and_unchanged_existing_thin_sections(self):
        old='### 概要\n\n'+'あ'*220
        self.assertEqual(h3_volume_regressions(old,'### 概要\n\n短い導入。\n\n#### 詳細\n\n具体的な回答。'),[])
        self.assertEqual(h3_volume_regressions('### 概要\n\n短い説明。','### 概要\n\n修正した説明。'),[])
    def test_injected_cta_does_not_fill_chapter_minimum(self):
        plan={'button_text':'登録する','url':'https://example.com/'}
        cta='\n\n---\n> '+'宣'*100+'\n>\n> **[登録する](https://example.com/)**\n---'
        self.assertTrue(h3_volume_regressions('### 商品\n\n'+'あ'*220+cta,'### 商品\n\n'+'あ'*170+cta,plan))
    def test_fenced_heading_does_not_create_fake_h4_exception(self):
        old='### 概要\n\n'+'あ'*220
        new='### 概要\n\n'+'あ'*170+'\n\n```markdown\n#### 例\n```'
        self.assertTrue(h3_volume_regressions(old,new))

    def test_normal_patch_application_rejects_thinning_before_saving_candidate(self):
        import json
        from pipeline.content_edits import content_blocks
        from pipeline.step_content_audit import apply_reviewed_edits
        from pipeline.content_quality import ContentQualityError
        before='## 主題\n\n### 説明\n\n'+'あ'*220
        block=content_blocks(before)[-1]
        raw=json.dumps({'edits':[{'id':block['id'],'new':'あ'*170}]})
        report={'checks':[{'key':'prose_quality','status':'fail','affected_blocks':[{'id':block['id'],'reason':'不要文を整理する'}]}]}
        with self.assertRaisesRegex(ContentQualityError,'h3_volume_regression'):
            apply_reviewed_edits(before,raw,None,report,[],'',before,None,{},None)
