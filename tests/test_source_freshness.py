import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import step_fact_sheet as sheet, step_fact_review as review
from pipeline.source_freshness import SOURCE_POLICY_VERSION
from pipeline.fresh_sources import FreshSources


TODAY = "2026-09-28"
OFFICIAL = "https://official.example/pricing"


def fact(value="月額2,000円", *, url=OFFICIAL, day=TODAY):
    return f"> {value}。出典：{url} ｜確認日：{day}｜確認箇所：「現在の料金は月額2,000円」｜[confirmed]"


class Block(SimpleNamespace):
    def model_dump(self):
        return vars(self)


class SourceFreshnessTests(unittest.TestCase):
    def check_fact(self, text, urls=None):
        return sheet._downgrade_incomplete_confirmations(
            text, searched_urls={OFFICIAL} if urls is None else urls, checked_on=TODAY,
        )

    def test_registered_price_and_self_written_url_are_not_verification(self):
        text, count = self.check_fact(fact("登録資料の月額1,000円"), urls=set())
        self.assertEqual(count, 1)
        self.assertNotIn("[confirmed]", text)
        self.assertIn("[hypothesis]", text)

    def test_current_search_evidence_survives_but_old_material_does_not(self):
        content = fact("登録資料では月額1,000円", url="https://old.example/pricing") + "\n\n" + fact()
        text, count = self.check_fact(content)
        self.assertEqual(count, 1)
        self.assertIn(fact(), text)
        self.assertEqual(text.count("[confirmed]"), 1)

    def test_upload_date_and_old_check_date_do_not_count_as_current_check(self):
        for content in (fact(day="2026-09-20"), fact().replace("確認日", "登録日"), fact(day="不明")):
            with self.subTest(content=content):
                self.assertEqual(self.check_fact(content)[1], 1)

    def test_fetch_date_alias_requires_direct_matching_evidence(self):
        fresh = FreshSources({}, [])
        fresh.pages[OFFICIAL] = {'url': OFFICIAL, 'status': 'success', 'text': '現在の料金は月額2,000円です。'}
        text = fact().replace('確認日', '取得日')
        self.assertEqual(sheet._downgrade_incomplete_confirmations(text, searched_urls=set(), checked_on=TODAY, fresh=fresh), (text, 0))
        self.assertEqual(self.check_fact(text)[1], 1)
        for invalid in (text.replace(TODAY, '2026-09-20'), text.replace('「現在の料金は月額2,000円」', '「取得本文にない根拠です」')):
            self.assertEqual(sheet._downgrade_incomplete_confirmations(invalid, searched_urls=set(), checked_on=TODAY, fresh=fresh)[1], 1)

    def test_unknown_evidence_is_not_confirmed(self):
        self.assertEqual(self.check_fact(fact().replace("「現在の料金は月額2,000円」", "不明"))[1], 1)

    def test_adjacent_facts_do_not_borrow_each_others_sources(self):
        content = fact() + "\n> 登録資料では会員数100万人。[confirmed]"
        text, count = self.check_fact(content)
        self.assertEqual(count, 1)
        self.assertIn(fact(), text)

    def test_japanese_dates_and_url_fragments_are_supported(self):
        text = fact(day="2026年9月28日", url=OFFICIAL + "/#plan")
        self.assertEqual(self.check_fact(text), (text, 0))

    def test_only_server_results_and_citations_supply_evidence_urls(self):
        blocks = [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": "https://fake.example/"}},
            {"type": "text", "text": "https://fake.example/"},
            {"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
            Block(type="web_search_tool_result", content=[{"type": "web_search_result", "url": OFFICIAL + "/#price"}]),
            {"type": "text", "citations": [{"type": "web_search_result_location", "url": "https://official.example/news"}]},
        ]
        self.assertEqual(sheet._search_result_urls(blocks), {OFFICIAL, "https://official.example/news"})

    def test_registered_material_is_labeled_unverified_and_truncation_is_explicit(self):
        text = sheet._build_primary_sources_prompt([{"title": "古い料金資料", "content_text": "古" * 2001}])
        self.assertIn("今回の確認は未実施", text)
        self.assertNotIn("他のweb検索データよりも優先", text)
        records = json.loads(text[text.index("[{"):])
        self.assertTrue(records[0]["truncated"])
        self.assertEqual(len(records[0]["content_excerpt"]), 2000)
        self.assertEqual(sheet._build_primary_sources_prompt([{"content_text": ""}]), "")

    def test_fact_sheet_pipeline_records_search_evidence_and_rejects_source_only_claim(self):
        output = fact() + "\n\n" + fact("古い資料の会員数100万人", url="https://old.example/members")
        response = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=20), stop_reason="end_turn",
            content=[Block(type="web_search_tool_result", content=[{"type": "web_search_result", "url": OFFICIAL}]),
                     Block(type="text", text=output)],
        )
        with patch.object(sheet, "get_artifact", return_value={"content_text": "テスト資料"}), \
             patch.object(sheet, "get_job", return_value={"tenant_id": "owner", "preset_id": "preset"}), \
             patch.object(sheet, "get_primary_sources_by_preset", return_value=[{"title": "旧資料", "content_text": "月額1,000円"}]) as sources, \
             patch.object(FreshSources, "save"), \
             patch.object(FreshSources, "fetch"), \
             patch.object(FreshSources, "prefetch", lambda self: self.pages.update({OFFICIAL: {"url": OFFICIAL, "status": "success", "text": "現在の料金は月額2,000円"}})), \
             patch.object(sheet, "current_check_date", return_value=TODAY), \
             patch.object(sheet.anthropic, "Anthropic"), \
             patch.object(sheet, "create_with_retry", return_value=response) as generate, \
             patch.object(sheet, "upsert_artifact", return_value={"id": "artifact"}) as save:
            sheet.run("job", "料金比較")
        sources.assert_called_once_with("owner", "preset")
        request = generate.call_args.kwargs
        self.assertIn(TODAY, request["messages"][0]["content"])
        self.assertIn("登録されているだけで[confirmed]にしない", request["system"])
        self.assertIn("【公式ソース必須の情報】", request["system"])
        self.assertIn("Tier 2のソース1件のみ", request["system"])
        stored = save.call_args.kwargs
        self.assertIn(fact(), stored["content_text"])
        self.assertEqual(stored["meta"]["incomplete_confirmations_downgraded"], 1)
        self.assertEqual(stored["meta"]["searched_source_urls"], [OFFICIAL])
        self.assertEqual(stored["meta"]["registered_source_count"], 1)
        self.assertEqual(stored["meta"]["source_policy_version"], SOURCE_POLICY_VERSION)

    def test_high_accuracy_passes_share_current_date_and_source_priority_rules(self):
        first = "===ARTICLE_START===\n修正本文\n===ARTICLE_END===\n===FACTCHECK_REPORT_START===\n公式で再確認\n===FACTCHECK_REPORT_END==="
        second = "===ARTICLE_START===\n最終本文\n===ARTICLE_END===\n===FINAL_AUDIT_START===\nPASS\n===FINAL_AUDIT_END==="
        resp = SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        with patch.object(review, "get_artifact", return_value={"content_text": "旧記事"}), \
             patch.object(review, "get_job", return_value={}), \
             patch.object(FreshSources, "save"), \
             patch.object(review, "current_check_date", return_value=TODAY), \
             patch.object(review.anthropic, "Anthropic"), \
             patch.object(review, "_run_search_pass", side_effect=[(resp, first, []), (resp, second, [])]) as passes, \
             patch.object(review, "upsert_artifact", return_value={"id": "artifact"}) as save:
            review.run("job", "料金比較")
        for call in passes.call_args_list:
            self.assertIn(TODAY, call.kwargs["prompt"])
            self.assertIn("登録資料や比較記事で古い数値へ戻さない", call.kwargs["system"])
        self.assertEqual(save.call_args_list[0].kwargs["meta"]["source_policy_version"], SOURCE_POLICY_VERSION)


if __name__ == "__main__":
    unittest.main()
