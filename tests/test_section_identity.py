from quality_fixtures import phases
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

class SectionPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        evidence=patch('pipeline.generation_context.generation_evidence',side_effect=lambda job,facts,sources,**kw:facts)
        evidence.start();self.addCleanup(evidence.stop)
        patcher=patch('pipeline.research_requirements.require_matrix',return_value={})
        patcher.start();self.addCleanup(patcher.stop)

    def test_renamed_section_survives_repair_and_final_gate(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from pipeline import step_content_audit,step_final_validate
        from pipeline.content_quality import EDITORIAL_CHECKS,CHECKS,POLICY_VERSION,snapshot
        artifacts={'article':{'content_text':TEXT,'meta':{'section_map':bind_sections(TEXT,OUTLINE)}},
                   'outline':{'content_text':OUTLINE},'content_contract':{'content_text':'{"required_sections":[]}'},
                   'fact_sheet':{'content_text':''},'fresh_sources':{'content_text':'[{"url":"https://example.com","status":"success","text":"原文"}]'}}
        calls=[]
        def audit(_,**kw):
            calls.append(kw['text'])
            return {'valid':len(calls)>1,'policy_version':POLICY_VERSION, 'stage':'article', 'phases':phases(snapshot(kw['text'],kw['facts'],kw['outline'],kw['contract'],kw['requirements'],kw['sources'])),
                    'snapshot':snapshot(kw['text'],kw['facts'],kw['outline'],kw['contract'],kw['requirements'],kw['sources']),
                    'checks':[{'key':k,'status':'fail' if len(calls)==1 and k=='conclusion_consistency' else 'pass','reason':'確認済み'} for k in CHECKS]}
        def save(**kw):artifacts[kw['step']]=kw;return kw
        response=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps({'edits':[{'id':'block-0000','new':TEXT.replace('気軽さ・会員数で選ぶならサービスA','機能を重視する人にはサービスA')}]}))])
        with patch.object(step_content_audit,'get_artifact',side_effect=lambda _,s:artifacts[s]),patch.object(step_content_audit,'get_job',return_value={}),patch.object(step_content_audit,'upsert_artifact',side_effect=save),patch.object(step_content_audit.anthropic,'Anthropic'),patch.object(step_content_audit,'audit',side_effect=audit),patch.object(step_content_audit,'create_with_retry',return_value=response):
            step_content_audit.run('j','比較')
        with patch.object(step_final_validate,'get_artifact',side_effect=lambda _,s:artifacts[s]),patch.object(step_final_validate,'get_job',return_value={}),patch.object(step_final_validate,'upsert_artifact',side_effect=save):
            step_final_validate.run('j','比較')
        self.assertTrue(json.loads(artifacts['structure_validation_final']['content_text'])['valid'])
        self.assertEqual(len(calls),2)
        self.assertIn('機能を重視',artifacts['article']['content_text'])
        self.assertEqual(artifacts['article']['meta']['section_map']['entries'][1]['id'],'section-001')
