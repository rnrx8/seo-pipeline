import json
import unittest
from pipeline.content_edits import content_blocks,apply_block_edits
from pipeline.readability import readability_issues
from pipeline.review_scope import repair_packet,validate_edits
from pipeline.content_quality import ContentQualityError

class RepairReadabilityScopeTests(unittest.TestCase):
    def setUp(self):
        self.text='# 記事\n\n## 選び方\n\n'+'あ'*160+'\n\n'+'い'*160+'\n\n| 項目 | 内容 |\n|---|---|\n| 料金 | 100円 |\n\n## 別の話題\n\n対象外。'
        self.blocks=content_blocks(self.text)

    def test_focused_report_routes_whole_current_run_without_manual_ids(self):
        packet=repair_packet(self.text,{},[],{'checks':[], 'readability_issues':readability_issues(self.text)})
        self.assertEqual(packet['target_block_ids'],[self.blocks[2]['id'],self.blocks[3]['id']])
        self.assertEqual(packet['source_documents'],[])
        self.assertNotIn(self.blocks[-1],packet['article_blocks'])
        raw=json.dumps({'edits':[{'id':self.blocks[2]['id'],'new':'あ'*120}]})
        validate_edits(raw,packet)
        self.assertFalse(readability_issues(apply_block_edits(self.text,raw)))
        with self.assertRaises(ContentQualityError):
            validate_edits(json.dumps({'edits':[{'id':self.blocks[-1]['id'],'new':'変更'}]}),packet)

    def test_stale_or_duplicate_run_is_recomputed_and_semantic_scope_preserved(self):
        stale={'key':'continuous_prose','affected_blocks':[{'id':'stale','reason':'old'}]}
        finding={'key':'organization','status':'fail','affected_blocks':[{'id':self.blocks[-1]['id'],'reason':'fixture'}]}
        packet=repair_packet(self.text,{},[],{'checks':[finding],'structural_issues':[stale,stale]})
        self.assertEqual(len(packet['structural_issues']),1)
        self.assertEqual(set(packet['target_block_ids']),{self.blocks[2]['id'],self.blocks[3]['id'],self.blocks[-1]['id']})

    def test_table_condition_destination_and_intro_are_editable_dependencies(self):
        text='## 比較\n\n次の表で料金を比べます。\n\n| 商品 | 料金 |\n|---|---|\n| A | 100円 |\n\n### A\n\n主要情報です。\n\n| 項目 | 内容 |\n|---|---|\n| 累計 | 100件 |\n\n補足です。\n\n100件は累計で、現在の件数ではありません。\n\n無関係な説明です。'
        blocks=content_blocks(text)
        table, destination, note = blocks[2], blocks[5], blocks[7]
        finding={'key':'prose_quality','status':'fail','affected_blocks':[
            {'id':table['id'],'reason':'比較する項目を整理する'},
            {'id':note['id'],'reason':f'条件を{destination["id"]}の累計セルへ移す'}]}
        packet=repair_packet(text,{},[],{'checks':[finding]})
        self.assertIn(blocks[1]['id'],packet['related_block_ids'])
        self.assertIn(destination['id'],packet['related_block_ids'])
        raw=json.dumps({'edits':[
            {'id':blocks[1]['id'],'new':'次の表で特徴を比べます。'},
            {'id':destination['id'],'new':destination['text'].replace('100件','100件（累計。現在の件数ではない）')},
            {'id':note['id'],'new':''}]})
        validate_edits(raw,packet)
        self.assertIn('現在の件数ではない',apply_block_edits(text,raw))
        # Being shown as surrounding context does not authorize unrelated edits.
        self.assertIn(blocks[-1]['id'],{b['id'] for b in packet['article_blocks']})
        with self.assertRaisesRegex(ContentQualityError,'指摘対象外'):
            validate_edits(json.dumps({'edits':[{'id':blocks[-1]['id'],'new':'変更'}]}),packet)

    def test_unknown_dependency_stops_without_whole_article_expansion(self):
        finding={'key':'prose_quality','status':'fail','affected_blocks':[
            {'id':self.blocks[2]['id'],'reason':'条件をblock-9999へ移す'}]}
        with self.assertRaisesRegex(ContentQualityError,'参照する段落ID'):
            repair_packet(self.text,{},[],{'checks':[finding]})

    def test_heading_before_table_is_not_an_editable_introduction(self):
        text='## 比較\n\n| 項目 | 内容 |\n|---|---|\n| 料金 | 100円 |'
        blocks=content_blocks(text)
        finding={'key':'unfinished_content','status':'fail','affected_blocks':[
            {'id':blocks[1]['id'],'reason':'比較表を整理する'}]}
        packet=repair_packet(text,{},[],{'checks':[finding]})
        self.assertEqual(packet['target_block_ids'],[blocks[1]['id']])
        with self.assertRaisesRegex(ContentQualityError,'指摘対象外'):
            validate_edits(json.dumps({'edits':[{'id':blocks[0]['id'],'new':'## 別の章'}]}),packet)
