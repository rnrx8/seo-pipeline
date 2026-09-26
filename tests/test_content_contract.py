import unittest

from pipeline.content_contract import build_content_contract, extract_service_candidates
from pipeline.step_article import _insert_repair_blocks
from pipeline.step_reference import extract_reference_urls
from pipeline.step_review import _word_count_action
from pipeline.step_service_map import _has_dedicated_service_h2
from pipeline.step_structure_guard import (
    deduplicate_service_h2s,
    extract_h2_titles,
    repair_outline,
    validate_structure,
)
from pipeline.step_outline import ensure_complete_volume_design


FACTS = """\
### 既婚者クラブ（Kikon Club）の会員数・料金
公式情報です。
### カドル（Cuddle）の会員数・マッチング数
公式情報です。
### ヒールメイト（Healmate）の会員数・属性
公式情報です。
"""


class ContentContractTests(unittest.TestCase):
    def test_named_pair_query_uses_standalone_bold_service_labels(self):
        facts = """### カドル（Cuddle）の会員数
## 主要な企業・サービス情報
**ヒールメイト（Healmate）**
- 特徴：Webサービス
**既婚者クラブ**
- 特徴：Webサービス
"""
        contract = build_content_contract(
            keyword="既婚者クラブ ヒールメイト", intent_text="Primary: 比較検討",
            query_attrs_text=None, fact_text=facts, job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        comparison = contract['required_sections'][0]
        self.assertEqual(comparison['candidate_services'], ['既婚者クラブ', 'ヒールメイト'])
        self.assertEqual(comparison['minimum_named_items'], 2)

    def test_transposed_comparison_table_handles_stale_candidates(self):
        contract = {'required_sections': [{'key': 'named_service_comparison',
                    'candidate_services': ['既婚者クラブ', '競合の参考データ'], 'minimum_named_items': 2}]}
        article = '## 基本情報を比較\n| 項目 | 既婚者クラブ | ヒールメイト |\n|---|---|---|\n| 形態 | Web | Web |'
        self.assertEqual(validate_structure(article, contract, outline=False), [])
        attributes = '## 基本情報を比較\n| 項目 | 既婚者クラブ | 特徴 |\n|---|---|---|\n| 形態 | Web | Web |'
        self.assertTrue(validate_structure(attributes, contract, outline=False))

    def test_comparison_cv_requires_comparison_and_service_coverage_not_dedicated_h2(self):
        contract = build_content_contract(
            keyword="既婚者 マッチングアプリ おすすめ",
            intent_text="- Primary（メイン意図）: Commercial\n- 比較検討したい",
            query_attrs_text='{"searcher_stage":"比較検討","key_concerns":["料金"]}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        self.assertEqual(
            [s["key"] for s in contract["required_sections"]],
            ["named_service_comparison", "featured_service_coverage"],
        )
        self.assertEqual(contract["service_treatment"], "comparison_featured")
        coverage = contract["required_sections"][1]
        self.assertFalse(coverage["dedicated_h2_required"])
        self.assertEqual(coverage["preferred_placement"], "within_comparison")

    def test_informational_cv_integrates_service_without_fixed_sections(self):
        contract = build_content_contract(
            keyword="マッチングアプリとは",
            intent_text="- Primary（メイン意図）: Informational",
            query_attrs_text='{"searcher_stage":"情報収集","key_concerns":[]}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        self.assertEqual([s["key"] for s in contract["required_sections"]], ["featured_service_coverage"])
        self.assertEqual(contract["service_treatment"], "integrated")
        self.assertEqual(contract["optional_sections"], [])

    def test_outline_parser_accepts_known_claude_formats(self):
        text = "\n".join([
            "## H2-1：比較A", "### H2-2. 比較B", "### H2-3｜比較C", "#### H2：比較D"
        ])
        self.assertEqual(extract_h2_titles(text, outline=True), ["比較A", "比較B", "比較C", "比較D"])

    def test_outline_repair_satisfies_contract(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        outline = "### H2-1：基礎知識\n\n### セクション別ボリューム設計\n"
        violations = validate_structure(outline, contract, outline=True)
        repaired, added = repair_outline(outline, contract, violations)
        self.assertEqual(set(added), {"named_service_comparison"})
        self.assertNotIn("既婚者クラブの特徴・料金・始め方", repaired)
        self.assertEqual(validate_structure(repaired, contract, outline=True), [])

    def test_existing_comparison_is_enriched_without_duplicate_h2(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        outline = (
            "### H2：おすすめアプリを比較\n"
            "#### H3：料金で選ぶ\n\n"
            "### H2：既婚者クラブがおすすめな理由\n"
        )
        violations = validate_structure(outline, contract, outline=True)
        repaired, _ = repair_outline(outline, contract, violations)
        self.assertEqual(repaired.count("### H2：おすすめアプリを比較"), 1)
        self.assertIn("カドル", repaired)
        self.assertIn("ヒールメイト", repaired)
        self.assertEqual(validate_structure(repaired, contract, outline=True), [])

    def test_reference_urls_require_reference_language(self):
        self.assertEqual(extract_reference_urls("詳しくは https://example.com"), [])
        self.assertEqual(extract_reference_urls("構成は https://example.com/a を参考に"), ["https://example.com/a"])

    def test_english_name_with_japanese_alias_is_a_service_candidate(self):
        facts = "#### ① Cuddle（カドル）\n#### ② Healmate（ヒールメイト）\n"
        self.assertEqual(extract_service_candidates(facts), ["Cuddle", "Healmate"])

    def test_service_fact_blocks_are_candidates_but_category_headings_are_not(self):
        facts = """\
## 主要な企業・サービス・求人情報
### 既婚者専用マッチングアプリ
> **カドル（Cuddle）**：2022年サービス開始。[confirmed]
> **カドルの料金（GOLD プラン・男性）**：月額料金。[hypothesis]
> **既婚者クラブ**：累計会員数約100万人。[confirmed]
> **ヒールメイト（Healmate）**：2022年6月リリース。[hypothesis]
### 出会い系アプリ（既婚者も利用可能）
> **ハッピーメール**：累計会員数3,500万人以上。[confirmed]
### 既婚者向けオフラインサービス（合コン・イベント）
> **キコンパ**（既婚者合コンサービス）：札幌でも開催。[confirmed]
## よくある誤解・注意点
> **【誤解①】サービスはすべて同じ**：誤り。
"""
        self.assertEqual(
            extract_service_candidates(facts),
            ["カドル", "既婚者クラブ", "ヒールメイト", "ハッピーメール", "キコンパ"],
        )

    def test_named_comparison_table_satisfies_stale_contract_candidates(self):
        contract = {
            "required_sections": [{
                "key": "named_service_comparison",
                "candidate_services": [
                    "既婚者クラブ", "既婚者専用マッチングアプリ", "出会い系アプリ",
                ],
                "minimum_named_items": 3,
            }],
        }
        article = """\
## 北海道で使える既婚者向けアプリ7選の比較

| サービス名 | 料金 | 特徴 |
|---|---|---|
| 既婚者クラブ | 8,880円〜 | 身バレ対策 |
| カドル | 有料 | AIマッチング |
| ヒールメイト | 一部無料 | 30〜50代中心 |
"""
        self.assertEqual(validate_structure(article, contract, outline=False), [])

    def test_truncated_volume_table_is_rebuilt_for_every_h2(self):
        outline = (
            "### H2：基礎\n### H2：アプリを比較\n### H2：サービスがおすすめな理由\n"
            "### セクション別ボリューム設計\n\n"
            "| H2タイトル | 重要度(1-5) | 推奨文字数 | 根拠（1文） |\n"
            "|---|---|---|---|\n| 基礎 | 4 | 900字 | 理由 |\n| アプリを比較 | 5"
        )
        repaired, changed = ensure_complete_volume_design(outline, "5,000〜7,000字")
        self.assertTrue(changed)
        self.assertEqual(repaired.count("| 検索意図と構造契約に基づく配分 |"), 3)
        self.assertIn("### H2：サービスがおすすめな理由", repaired)

    def test_word_count_compression_protects_contract_sections(self):
        action = _word_count_action(9000, "5,000〜7,000字")
        self.assertIn("protected=true", action)
        self.assertNotIn("H2またはH3セクションを丸ごと削除", action)

    def test_recommended_service_h2_counts_as_dedicated(self):
        for phrase in ("おすすめな理由", "おすすめの理由", "おすすめする理由"):
            outline = f"### H2：北海道なら既婚者クラブを{phrase}\n"
            self.assertTrue(_has_dedicated_service_h2(outline, "既婚者クラブ"))

    def test_recommendation_heading_variants_satisfy_service_coverage(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        for phrase in ("おすすめな理由", "おすすめの理由", "おすすめする理由"):
            with self.subTest(phrase=phrase):
                outline = (
                    "### H2：おすすめアプリを比較\n"
                    "#### H3：既婚者クラブの特徴と向いている人\n"
                    "#### H3：カドルの特徴\n"
                    "#### H3：ヒールメイトの特徴\n"
                    f"### H2：北海道で既婚者クラブを{phrase}\n"
                )
                self.assertEqual(validate_structure(outline, contract, outline=True), [])

    def test_service_h3_inside_comparison_avoids_separate_h2(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        outline = """\
### H2：既婚者向けアプリ3社を比較
#### H3：料金・安全性の比較表
| サービス名 | 料金 |
|---|---|
| 既婚者クラブ | 有料 |
| カドル | 有料 |
| ヒールメイト | 一部無料 |
#### H3：既婚者クラブがおすすめの理由と向いている人
- セクション内容：強みと向いている人を説明する。
"""
        violations = validate_structure(outline, contract, outline=True)
        self.assertEqual(violations, [])
        repaired, added = repair_outline(outline, contract, violations)
        self.assertEqual(repaired, outline)
        self.assertEqual(added, [])
        self.assertEqual(repaired.count("既婚者クラブ"), 2)

    def test_missing_service_coverage_is_integrated_into_existing_comparison(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        outline = """\
### H2：既婚者向けアプリを比較
#### H3：料金・安全性の比較表
| サービス名 | 料金 |
|---|---|
| 既婚者クラブ | 有料 |
| カドル | 有料 |
| ヒールメイト | 一部無料 |
### H2：利用時の注意点
"""
        violations = validate_structure(outline, contract, outline=True)
        self.assertEqual([item["key"] for item in violations], ["featured_service_coverage"])
        repaired, added = repair_outline(outline, contract, violations)
        self.assertIn("featured_service_coverage_integrated", added)
        self.assertIn("#### H3：既婚者クラブがおすすめな理由と向いている人", repaired)
        self.assertLess(repaired.index("おすすめな理由と向いている人"), repaired.index("### H2：利用時の注意点"))
        self.assertNotIn("### H2：既婚者クラブの特徴・料金・始め方", repaired)
        self.assertEqual(validate_structure(repaired, contract, outline=True), [])

    def test_duplicate_service_h2_is_detected_and_generic_block_removed(self):
        contract = build_content_contract(
            keyword="既婚者 アプリ おすすめ",
            intent_text="Primary（メイン意図）: Commercial",
            query_attrs_text='{"searcher_stage":"比較検討"}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        outline = """\
### H2：おすすめアプリを比較
#### H3：既婚者クラブの特徴と向いている人
#### H3：カドルの特徴
### H2：北海道なら既婚者クラブがおすすめの理由
#### H3：地域で選ぶ理由
### H2：既婚者クラブの特徴・料金・始め方
#### H3：既婚者クラブの料金
### H2：まとめ
"""
        violations = validate_structure(outline, contract, outline=True)
        self.assertIn("featured_service_duplicate_h2", [item["key"] for item in violations])
        repaired, added = repair_outline(outline, contract, violations)
        self.assertIn("featured_service_duplicate_h2_removed", added)
        self.assertIn("北海道なら既婚者クラブがおすすめの理由", repaired)
        self.assertNotIn("既婚者クラブの特徴・料金・始め方", repaired)
        self.assertEqual(validate_structure(repaired, contract, outline=True), [])

    def test_article_duplicate_cleanup_preserves_natural_h2_and_summary(self):
        article = """\
## 北海道なら既婚者クラブがおすすめの理由
地域との相性や主な強みを説明します。
## 既婚者クラブの特徴・料金・始め方
汎用的な重複説明です。
## まとめ
結論です。
"""
        repaired, removed = deduplicate_service_h2s(article, "既婚者クラブ", outline=False)
        self.assertEqual(removed, ["既婚者クラブの特徴・料金・始め方"])
        self.assertIn("北海道なら既婚者クラブがおすすめの理由", repaired)
        self.assertIn("## まとめ\n結論です。", repaired)
        self.assertNotIn("汎用的な重複説明", repaired)

    def test_article_service_repair_is_inserted_inside_comparison_h2(self):
        article = """\
## アプリ3社を比較
比較表です。
## 利用時の注意点
注意事項です。
"""
        repaired = _insert_repair_blocks(
            article,
            "### 既婚者クラブがおすすめの理由\n具体的な強みです。",
            [{"key": "featured_service_coverage"}],
        )
        self.assertLess(repaired.index("### 既婚者クラブ"), repaired.index("## 利用時の注意点"))


if __name__ == "__main__":
    unittest.main()
