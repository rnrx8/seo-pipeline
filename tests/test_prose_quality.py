import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import content_quality as q
from pipeline import step_cta_inject as cta
from pipeline.content_edits import apply_block_edits


class ProseRegressionTests(unittest.TestCase):
    def test_monitoring_is_not_successful_elimination(self):
        self.assertTrue(q.explicit_risk_guarantees('24時間365日の監視体制でサクラや業者、美人局を排除しています。'))
        for text in ['監視体制で不正利用者の排除に取り組んでいます。',
                     'サクラや業者を排除していますとは保証できません。',
                     '「業者を排除しているサービスを使いたい」という希望があります。',
                     '通報された当該アカウントを削除しました。']:
            self.assertFalse(q.explicit_risk_guarantees(text), text)

    def test_fluent_prose_failure_cannot_inherit_factual_pass(self):
        source={'checks':[{'key':k,'status':'pass','reason':'根拠と一致'} for k in q.CHECKS]}
        focused={'checks':[{'key':k,'status':'fail' if k=='prose_quality' else 'pass',
                           'reason':'読者分析ラベルが不自然な呼びかけになっている',
                           'affected_blocks':[{'id':'block-0002','reason':'呼びかけの意味が不明'}] if k=='prose_quality' else []}
                          for k in q.EDITORIAL_CHECKS]}
        def response(value):
            return SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps(value))],
                                   usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(q,'create_with_retry',side_effect=[response(source),response(focused)]):
            result=q.audit(None,stage='article',text='# 記事\n\n見えない課金に敏感な方でも納得できます。',facts='',outline='',contract={},requirements={})
        self.assertFalse(result['valid'])
        self.assertEqual(next(c for c in result['checks'] if c['key']=='prose_quality')['affected_blocks'][0]['id'],'block-0002')
        with self.assertRaises(q.ContentQualityError):q.require_audit(result,result['snapshot'],stage='article')

    def test_final_rejects_skipped_prose_or_missing_focused_audit(self):
        report={'checks':[{'key':k,'status':'pass','reason':'確認済み'} for k in q.CHECKS],
                'stage':'article','valid':True,'snapshot':'hash','policy_version':q.POLICY_VERSION,
                'editorial_audit':{'checks':[{'key':k,'status':'pass'} for k in q.EDITORIAL_CHECKS]}}
        q.require_audit(report,'hash',stage='article')
        report['checks'][-1]['status']='not_applicable'
        with self.assertRaises(q.ContentQualityError):q.require_audit(report,'hash',stage='article')
        report['checks'][-1]['status']='pass';report.pop('editorial_audit')
        with self.assertRaises(q.ContentQualityError):q.require_audit(report,'hash',stage='article')

    def test_duplicate_body_can_be_removed_without_deleting_heading(self):
        text='# 記事\n\n共感の繰り返し。\n\n## 比較\n\n料金を比較します。'
        result=apply_block_edits(text,json.dumps({'edits':[{'id':'block-0002','new':''}]}))
        self.assertNotIn('共感の繰り返し',result)
        self.assertIn('## 比較',result)
        with self.assertRaises(q.ContentQualityError):apply_block_edits(text,json.dumps({'edits':[{'id':'block-0004','new':''}]}))

    def test_cta_targets_do_not_match_other_similar_headings(self):
        article='# 記事\n\n## 料金と安全性で比較\n\n説明です。\n\n## 安全性で選ぶための比較\n\n別の説明です。'
        config={'body':'登録できます。','button_text':'登録','url':'https://example.com/'}
        result,count=cta._insert_cta_after_h2s(article,['料金と安全性で比較'],cta._format_cta_block(config),config)
        self.assertEqual(count,1)
        self.assertEqual(result.count('[登録]'),1)
        repeated,count=cta._insert_cta_after_h2s(result,['料金と安全性で比較'],cta._format_cta_block(config),config)
        self.assertEqual(count,0)
        self.assertEqual(repeated,result)
        plan={**config,'count':1}
        self.assertFalse(cta.cta_placement_issues(result,plan))
        self.assertTrue(cta.cta_placement_issues(result+'\n'+cta._format_cta_block(config),plan))
        with self.assertRaises(q.ContentQualityError):cta._insert_cta_after_h2s(article,['存在しない章'],'CTA',config)

    def test_cta_source_link_does_not_prevent_button_insertion(self):
        config={'body':'登録できます。','button_text':'無料登録','url':'https://example.com/'}
        text='## 比較\n出典: https://example.com/'
        result,count=cta._insert_cta_after_h2s(text,['比較'],cta._format_cta_block(config),config)
        self.assertEqual(count,1)
        self.assertIn('[無料登録]',result)

    def test_cta_stays_bound_to_section_after_heading_correction(self):
        from pipeline.section_identity import bind_sections, carry_sections
        outline='### H2：料金\n### H2：まとめ'
        text='## 料金\n\n説明。\n\n## まとめ\n\n> [登録](https://example.com/)'
        binding=bind_sections(text,outline)
        plan={'button_text':'登録','url':'https://example.com/','count':1,'section_ids':['section-001']}
        self.assertFalse(cta.cta_placement_issues(text,plan,binding))
        changed=text.replace('## まとめ','## 選ぶ際の要点')
        carried=carry_sections(text,changed,outline,binding)
        self.assertFalse(cta.cta_placement_issues(changed,plan,carried))
        moved='## 料金\n\n> [登録](https://example.com/)\n\n## まとめ\n\n説明。'
        self.assertTrue(cta.cta_placement_issues(moved,plan,binding))

    def test_cta_target_resolves_corrected_heading_by_identity(self):
        from pipeline.section_identity import bind_sections, carry_sections
        outline='### H2：料金\n### H2：まとめ'
        before='## 料金\n説明。\n## まとめ\n結論。'
        after=before.replace('## まとめ','## 選択するときの要点')
        binding=carry_sections(before,after,outline,bind_sections(before,outline))
        self.assertEqual(cta.resolve_cta_targets(['まとめ'],outline,binding),['選択するときの要点'])

    def test_repair_cannot_introduce_adjacent_duplicate_paragraph(self):
        from pipeline.article_quality import validate_delivery
        paragraph='無料登録は複数のサービスを併用できます。どれか1つに絞る必要はないため、気になる候補を試して会員層を比較してください。'
        outline='### H2：選び方'
        text='## 選び方\n\n'+paragraph+'\n\n'+paragraph
        self.assertIn('duplicate_prose',{v['key'] for v in validate_delivery(text,outline)})
        self.assertNotIn('duplicate_prose',{v['key'] for v in validate_delivery('## 選び方\n\n'+paragraph,outline)})
        quoted='## 選び方\n\n> '+paragraph+'\n\n> '+paragraph
        self.assertNotIn('duplicate_prose',{v['key'] for v in validate_delivery(quoted,outline)})

    def test_audit_prompt_change_invalidates_saved_snapshot(self):
        before=q.snapshot('article','facts','outline',{}, {})
        with patch.object(q,'EDITORIAL_SYSTEM',q.EDITORIAL_SYSTEM+'\n新しい必須検査'):
            self.assertNotEqual(before,q.snapshot('article','facts','outline',{}, {}))
