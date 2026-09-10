import unittest

from pipeline.content_contract import build_content_contract, extract_service_candidates
from pipeline.step_reference import extract_reference_urls
from pipeline.step_review import _word_count_action
from pipeline.step_service_map import _has_dedicated_service_h2
from pipeline.step_structure_guard import extract_h2_titles, repair_outline, validate_structure
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
    def test_comparison_cv_requires_named_comparison_and_dedicated_service(self):
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
            ["named_service_comparison", "featured_service_dedicated"],
        )
        self.assertEqual(contract["service_treatment"], "dedicated")

    def test_informational_cv_integrates_service_without_fixed_sections(self):
        contract = build_content_contract(
            keyword="マッチングアプリとは",
            intent_text="- Primary（メイン意図）: Informational",
            query_attrs_text='{"searcher_stage":"情報収集","key_concerns":[]}',
            fact_text=FACTS,
            job={"article_purpose": "CV"},
            service={"name": "既婚者クラブ"},
        )
        self.assertEqual([s["key"] for s in contract["required_sections"]], ["featured_service_integrated"])
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
        self.assertEqual(set(added), {"named_service_comparison", "featured_service_dedicated"})
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
        outline = "### H2：北海道なら既婚者クラブがおすすめな理由\n"
        self.assertTrue(_has_dedicated_service_h2(outline, "既婚者クラブ"))


if __name__ == "__main__":
    unittest.main()
