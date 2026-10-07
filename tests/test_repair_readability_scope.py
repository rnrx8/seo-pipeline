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
