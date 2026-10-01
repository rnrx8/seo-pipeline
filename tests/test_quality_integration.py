import json

SOURCE = {"content_text": json.dumps([{"url": "https://official.example/", "status": "success", "text": "直接取得した原文です"}])}
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import step_fact_review, step_service_map, step_content_audit
from pipeline.content_quality import audit_facts, digest, ContentQualityError, requirements_for, source_evidence, snapshot, writing_evidence
from pipeline.fresh_sources import FreshSources


class QualityIntegrationTests(unittest.TestCase):
    def test_corrected_facts_exclude_original_wrong_claim_and_preserve_evidence(self):
        fresh = FreshSources({}, [])
        fresh.pages['https://example.com/price'] = {'url':'https://example.com/price', 'status':'success',
            'text':'現在の料金は月額2,000円です。'}
        report = '''### Claim 1
- 元の主張: 月額1,000円です。
- 判定: CORRECTED
- 修正後: 月額2,000円です。
- 根拠: https://example.com/price
- 確認箇所: 「現在の料金は月額2,000円です。」'''
        facts = step_fact_review._verified_facts(report, fresh)
        self.assertEqual(list(facts), ['1'])
        self.assertNotIn('1,000', facts['1'])
        self.assertIn('2,000', facts['1'])
        base = '> 月額1,000円 [confirmed]'
        evidence = {'content_text':facts['1'], 'meta':{'base_fact_sha256':digest(base)}}
        article = {'meta':{'fact_review_evidence_sha256':digest(facts['1'])}}
        combined = audit_facts(base, article, high_accuracy=True, evidence=evidence)
        self.assertIn('以下を優先', combined)
        self.assertIn('月額2,000円です。', combined)
        # Content-only correction retains evidence lineage; a new research or
        # writer result cannot inherit it from the old job's artifacts.
        with self.assertRaises(ContentQualityError):
            audit_facts(base+' changed', article, high_accuracy=True, evidence=evidence)
        with self.assertRaises(ContentQualityError):
            audit_facts(base, {'meta':{}}, high_accuracy=True, evidence=evidence)
        self.assertNotIn('2,000', audit_facts(base, article, high_accuracy=False, evidence=evidence))

    def test_correction_without_matching_direct_quote_is_rejected(self):
        fresh = FreshSources({}, [])
        report = '### Claim 1\n- 判定: CORRECTED\n- 修正後: 500円\n- 根拠: https://example.com\n- 確認箇所: 「料金は500円です」'
        with self.assertRaises(ContentQualityError):step_fact_review._verified_facts(report, fresh)

    def test_service_map_cannot_reintroduce_hypothesis_via_placement_instructions(self):
        artifacts = {'outline':{'content_text':'### H2：料金の比較'},
                     'fresh_sources':SOURCE, 'fact_sheet':{'content_text':'> 月額500円 [confirmed]\n\n> 月額123456円 [hypothesis]'}}
        response = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
            'service_section_type':'none','primary_h2':'','per_section_instructions':{},'cta_after_h2':[]}))],
            usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(step_service_map,'get_artifact',side_effect=lambda _,s:artifacts[s]), \
             patch.object(step_service_map,'get_job',return_value={}), \
             patch.object(step_service_map.anthropic,'Anthropic'), \
             patch.object(step_service_map,'create_with_retry',return_value=response) as model, \
             patch.object(step_service_map,'upsert_artifact',side_effect=lambda **kw:{'id':'x',**kw}):
            step_service_map.run('j','比較')
        prompt=model.call_args.kwargs['messages'][0]['content']
        self.assertIn('500円',prompt)
        self.assertNotIn('123456',prompt)

    def test_semantic_repair_pass_sets_consistent_article_metadata(self):
        from pipeline.content_quality import CHECKS
        artifacts={'article':{'content_text':'## 比較\n条件を揃えます。','meta':{'content_repaired':True}},
                   'fresh_sources':SOURCE, 'fact_sheet':{'content_text':''},'outline':{'content_text':'### H2：比較'},
                   'content_contract':{'content_text':'{"required_sections":[]}'}}
        report={'valid':True,'snapshot':'verified','checks':[{'key':k,'status':'pass','reason':'確認済み'} for k in CHECKS]}
        with patch.object(step_content_audit,'get_artifact',side_effect=lambda _,s:artifacts[s]), \
             patch.object(step_content_audit,'get_job',return_value={}), \
             patch.object(step_content_audit.anthropic,'Anthropic'), \
             patch.object(step_content_audit,'audit',return_value=report), \
             patch.object(step_content_audit,'upsert_artifact',side_effect=lambda **kw:kw) as save:
            step_content_audit.run('j','比較')
        final=[c.kwargs for c in save.call_args_list if c.kwargs['step']=='article'][-1]
        self.assertTrue(final['meta']['content_audited'])
        self.assertTrue(final['meta']['content_repaired'])
        self.assertEqual(final['meta']['content_audit_snapshot'],'verified')

    def test_fetched_bodies_and_later_corrections_reach_audit_and_snapshot(self):
        first = {'content_text': json.dumps([{'url': 'https://official.example/', 'status': 'success', 'text': '女性は無料。男性は有料。'}])}
        later = {'content_text': json.dumps([{'url': 'https://official.example/', 'status': 'success', 'text': '女性の基本機能は無料です。'}])}
        current = source_evidence(first, later)
        self.assertIn('女性の基本機能は無料です。', current)
        self.assertNotIn('男性は有料', current)
        self.assertNotEqual(snapshot('text','facts','outline',{}, {}, source_evidence(first)), snapshot('text','facts','outline',{}, {}, current))

    def test_missing_direct_source_bodies_fail_closed(self):
        with self.assertRaises(ContentQualityError):
            source_evidence({'content_text':'[{"url":"https://example.com", "status":"failed"}]'})

    def test_large_source_context_is_bounded_and_marks_omitted_text(self):
        rows=[{'url':f'https://example.com/{n}', 'status':'success','text':'a'*25000+'末尾の料金表'} for n in range(10)]
        result=json.loads(source_evidence({'content_text':json.dumps(rows)}))
        self.assertLess(sum(len(p['text']) for p in result),181000)
        self.assertTrue(all(p['truncated'] and p['text'].endswith('末尾の料金表') for p in result))

    def test_writer_and_editor_can_access_the_sources_seen_by_auditor(self):
        text=writing_evidence('> 未確認の価格123456円 [hypothesis]\n\n> 基本機能あり [confirmed]',
                              source_evidence({'content_text':json.dumps([{'url':'https://official.example/', 'status':'success', 'text':'女性の基本料金は無料、男性は有料です。'}])}))
        self.assertIn('女性の基本料金は無料',text)
        self.assertIn('基本機能あり',text)
        self.assertNotIn('123456',text)

    def test_quoted_product_definition_retains_named_comparison_partner(self):
        from pipeline.content_contract import extract_service_candidates
        facts='> **「ヒールメイト（Healmate）」とは**：既婚者専用サービス。\n### ■ ヒールメイトの男女比（公式公表値）'
        self.assertEqual(extract_service_candidates(facts,'既婚者クラブ'),['既婚者クラブ','ヒールメイト'])
        self.assertEqual(extract_service_candidates('### ■ ヒールメイトの男女比（公式公表値）','既婚者クラブ'),['既婚者クラブ','ヒールメイト'])

    def test_property_labels_cannot_inflate_required_service_count(self):
        from pipeline.content_contract import extract_service_candidates
        facts='## 主要な企業・サービス情報\n> **累計マッチング数**：840万組\n> **アクティブユーザー率**：75%\n> **安全対策**：本人確認\n> **無料会員と有料会員の違い**：あり\n### ▼ 会員数・マッチング数（ヒールメイト）\n> 確認済み\n### JAPHICマーク取得\n### 既婚者クラブ独自'
        self.assertEqual(extract_service_candidates(facts,'既婚者クラブ'),['既婚者クラブ','ヒールメイト'])

    def test_editorial_constraints_are_part_of_audit_requirements(self):
        job={'citation_style':'inline','target_audience':'初心者','tone_style':'丁寧','service_id':'a','cta_id':'b'}
        self.assertTrue(job.items() <= requirements_for(job,'比較').items())


if __name__=='__main__':unittest.main()
