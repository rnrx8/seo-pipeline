import json
import unittest
from unittest.mock import patch, Mock
from pipeline.content_edits import content_blocks,apply_block_edits
from pipeline.readability import readability_issues
from pipeline.review_scope import repair_packet,validate_edits
from pipeline.content_quality import ContentQualityError

class RepairReadabilityScopeTests(unittest.TestCase):
    def test_grouped_prose_finding_preserves_an_unchanged_correct_paragraph(self):
        from pipeline.step_content_audit import apply_reviewed_edits
        text='## 説明\n\n'+'あ'*140+'\n\n'+'い'*170
        blocks=content_blocks(text);ids=[b['id'] for b in blocks[1:]]
        report={'checks':[{'key':'prose_quality','status':'fail','affected_blocks':[{'id':i,'reason':'文章の連続を整理'} for i in ids]}],
                'editorial_findings':[{'severity':'blocking','block_ids':ids}]}
        raw=json.dumps({'edits':[{'id':ids[0],'new':'あ'*120}]})
        with patch('pipeline.step_content_audit.carry_sections',return_value={}):
            result,_=apply_reviewed_edits(text,raw,None,report,readability_issues(text),'',text,None,{},None)
        self.assertIn('い'*170,result)
        self.assertEqual(readability_issues(result),[])
        # Merely touching a run does not waive the deterministic full-text check.
        raw=json.dumps({'edits':[{'id':ids[0],'new':'あ'*139}]})
        with self.assertRaisesRegex(ContentQualityError,'continuous_prose'):
            apply_reviewed_edits(text,raw,None,report,readability_issues(text),'',text,None,{},None)

    def test_one_group_cannot_stand_in_for_a_separate_unedited_defect(self):
        from pipeline.step_content_audit import apply_reviewed_edits
        text='## 説明\n\n一つめ。\n\n二つめ。'
        blocks=content_blocks(text);ids=[b['id'] for b in blocks[1:]]
        report={'checks':[{'key':'prose_quality','status':'fail','affected_blocks':[{'id':i,'reason':'意味の誤り'} for i in ids]}],
                'editorial_findings':[{'severity':'blocking','block_ids':[i]} for i in ids]}
        raw=json.dumps({'edits':[{'id':ids[0],'new':'直した一つめ。'}]})
        with self.assertRaisesRegex(ContentQualityError,'問題の修正がありません'):
            apply_reviewed_edits(text,raw,None,report,[],'',text,None,{},None)

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

    def test_heading_without_blank_line_carries_actual_parent_without_new_ids(self):
        from pipeline.review_scope import scoped_packet
        text='# 記事\n\n## 過去の章\n\n前の章の説明。\n## 利用実態：調査と体験談\n\n### 利用者の体験\n\n導入。\n\n| 体験 | 意味 |\n|---|---|\n| A | B |'
        blocks=content_blocks(text)
        packet=scoped_packet({'article_blocks':blocks,'source_documents':[]},
            [{'affected_blocks':[{'id':blocks[-1]['id'],'reason':'体験の照合'}]}])
        self.assertIn(blocks[2],packet['heading_context'])
        self.assertNotIn(blocks[1],packet['heading_context'])
        self.assertEqual(packet['target_block_ids'],[blocks[-1]['id']])
        self.assertEqual(content_blocks(text),blocks)

    def test_fenced_heading_example_does_not_replace_parent(self):
        from pipeline.review_scope import scoped_packet
        text='# 記事\n\n## 実際の章\n\n```markdown\n例文\n\n## コード内の見出し\n```\n\n説明。\n\n対象。'
        blocks=content_blocks(text)
        packet=scoped_packet({'article_blocks':blocks,'source_documents':[]},
            [{'affected_blocks':[{'id':blocks[-1]['id'],'reason':'照合'}]}])
        self.assertIn(blocks[1],packet['heading_context'])
        self.assertNotIn(blocks[3],packet['heading_context'])

    def test_normal_repair_preparation_does_not_send_or_save_audit(self):
        from pipeline import step_content_audit as audit
        report={'checks':[{'key':'prose_quality','status':'fail',
            'affected_blocks':[{'id':self.blocks[-1]['id'],'reason':'修飾関係を整える'}]}]}
        client=Mock()
        with patch('pipeline.quality_context.compact_enabled',return_value=True), \
             patch.object(audit,'hybrid_writing_enabled',return_value=True), \
             patch.object(audit,'get_step_config',return_value=('claude-opus-5-5',6000)), \
             patch.object(audit,'create_with_retry') as send,patch.object(audit,'upsert_artifact') as save:
            request,packet=audit.prepare_content_repair(client,job_id='cost-only',
                text=self.text,report=report,requirements={},sources=[],facts='',contract={},issues=[])
        send.assert_not_called();save.assert_not_called();client.assert_not_called()
        client.with_options.assert_not_called()
        self.assertEqual(request['max_tokens'],6000)
        self.assertTrue(request['output_budget_includes_thinking'])
        self.assertIn(self.blocks[-1]['id'],packet['target_block_ids'])
        self.assertNotIn('snapshot',report)
        from pipeline.evidence_policy import EVIDENCE_POLICY
        from pipeline.fresh_sources import PUBLICATION_WRITING_POLICY
        self.assertIn(PUBLICATION_WRITING_POLICY,request['system'])
        self.assertNotIn(EVIDENCE_POLICY,request['system'])

    def test_repair_preparation_rejects_unknown_finding_before_side_effects(self):
        from pipeline import step_content_audit as audit
        report={'checks':[{'key':'prose_quality','status':'fail',
            'affected_blocks':[{'id':'block-9999','reason':'指摘'}]}]}
        with patch('pipeline.quality_context.compact_enabled',return_value=True), \
             patch.object(audit,'create_with_retry') as send,patch.object(audit,'upsert_artifact') as save:
            with self.assertRaises(ContentQualityError):
                audit.prepare_content_repair(Mock(),job_id='cost-only',text=self.text,
                    report=report,requirements={},sources=[],facts='',contract={},issues=[])
        send.assert_not_called();save.assert_not_called()
