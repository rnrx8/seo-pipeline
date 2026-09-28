import json
import os
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from pipeline import serp_sources, step_serp
from pipeline.autofix import classify_error


def result(host):
    return {"title": host, "link": f"https://{host}/", "snippet": "example"}


def response(data, status=200):
    return Mock(ok=status == 200, status_code=status, json=Mock(return_value=data))


class SerpSourceTests(unittest.TestCase):
    def test_recorded_mismatches_stop_before_generation(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures/serp-mismatch-20260926.json').read_text())
        for name, pair in fixtures.items():
            keyword = pair['standard']['search_parameters']['q']
            with self.subTest(keyword=name), patch.dict(os.environ, {'SERP_PROVIDER': 'serpapi'}), patch.object(serp_sources, '_serpapi', side_effect=[pair['standard'], pair['probe']]):
                with self.assertRaises(serp_sources.SerpQualityError):
                    serp_sources.fetch_serp(keyword)

    def test_serper_preserves_order_and_original_query(self):
        raw = {"searchParameters": {"q": "既婚者 アプリ"}, "organic": [result("one.jp"), result("two.jp")],
               "peopleAlsoAsk": [{"question": "質問"}], "relatedSearches": [{"query": "関連語"}]}
        with patch.dict(os.environ, {"SERP_PROVIDER": "serper", "SERPER_API_KEY": "secret"}), patch.object(serp_sources.requests, 'post', return_value=response(raw)) as post:
            data, source = serp_sources.fetch_serp("既婚者 アプリ")
        self.assertEqual(data["organic_results"], raw["organic"])
        self.assertEqual(source["search_mode"], "standard")
        self.assertEqual(post.call_args.kwargs["json"], {"q": "既婚者 アプリ", "gl": "jp", "hl": "ja", "num": 10})
        self.assertEqual(data["related_questions"], raw["peopleAlsoAsk"])

    def test_serpapi_disjoint_probe_stops_instead_of_substituting_verbatim(self):
        normal = {"organic_results": [result(f"wrong{i}.jp") for i in range(9)]}
        probe = {"organic_results": [result(f"right{i}.jp") for i in range(10)]}
        with patch.dict(os.environ, {"SERP_PROVIDER": "serpapi", "SERPAPI_KEY": "secret"}), patch.object(serp_sources.requests, 'get', side_effect=[response(normal), response(probe)]):
            with self.assertRaises(serp_sources.SerpQualityError):
                serp_sources.fetch_serp("検索語")

    def test_guard_failure_does_not_fetch_headings_or_save(self):
        with patch.object(step_serp, 'get_job', return_value={}), patch.object(step_serp, 'get_optional_artifact', return_value=None), patch.object(step_serp, 'fetch_serp', side_effect=serp_sources.SerpQualityError('不整合')), patch.object(step_serp, '_fetch_headings') as fetch, patch.object(step_serp, 'upsert_artifact') as save:
            with self.assertRaises(serp_sources.SerpQualityError):
                step_serp.run('job', '検索語')
        fetch.assert_not_called()
        save.assert_not_called()

    def test_pagination_query_change_is_rejected(self):
        data = {"organic_results": [result('example.jp')], "pagination": {"next": "https://www.google.com/search?q=other&start=10"}}
        with self.assertRaises(serp_sources.SerpQualityError):
            serp_sources.validate_results(data, "original")

    def test_empty_error_duplicate_and_private_urls_are_rejected(self):
        for data in [{"error": "provider failure"}, {"organic_results": []},
                     {"organic_results": [result('same.jp'), result('same.jp')]},
                     {"organic_results": [result('127.0.0.1')]},
                     {"organic_results": [result('localhost')]},
                     {"organic_results": [{"title": "title", "link": "javascript:alert(1)"}]}]:
            with self.subTest(data=data), self.assertRaises(serp_sources.SerpQualityError):
                serp_sources.validate_results(data, "query")

    def test_browser_override_job_query_and_expiry(self):
        snapshot = {"job_id": "job", "query": "検索語", "observed_at": datetime.now(timezone.utc).isoformat(),
                    "search_url": "https://www.google.co.jp/search?" + urlencode({"q": "検索語"}),
                    "organic_results": [result('example.jp')]}
        data, source = serp_sources.verified_serp(snapshot, '検索語', 'job')
        self.assertEqual(source['validation'], 'browser_verified')
        for bad in [{**snapshot, 'job_id': 'other'}, {**snapshot, 'query': 'other'},
                    {**snapshot, 'observed_at': (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()},
                    {**snapshot, 'search_url': snapshot['search_url']+'&tbs=li:1'}]:
            with self.subTest(bad=bad), self.assertRaises(serp_sources.SerpQualityError):
                serp_sources.verified_serp(bad, '検索語', 'job')

    def test_provider_errors_do_not_echo_key(self):
        with patch.dict(os.environ, {"SERP_PROVIDER": "serpapi", "SERPAPI_KEY": "TOP_SECRET"}), patch.object(serp_sources.requests, 'get', return_value=response({},401)):
            with self.assertRaises(serp_sources.SerpQualityError) as error:
                serp_sources.fetch_serp('query')
        self.assertNotIn('TOP_SECRET', str(error.exception))

    def test_quality_errors_do_not_trigger_retries_or_code_autofix(self):
        info = classify_error(serp_sources.SerpQualityError('不整合'))
        self.assertFalse(info['retryable'])
        self.assertEqual(info['type'], 'operational')

    def test_missing_paa_stays_empty_and_source_is_saved(self):
        data = {'organic_results': [result('example.jp')]}
        source = {'provider': 'serper', 'search_mode': 'standard'}
        with patch.object(step_serp, 'get_job', return_value={}), patch.object(step_serp, 'get_optional_artifact', return_value=None), patch.object(step_serp, 'fetch_serp', return_value=(data,source)) as fetch, patch.object(step_serp, '_fetch_headings', return_value={'fetch_status': 'success'}), patch.object(step_serp, 'upsert_artifact', return_value={'id':'artifact'}) as save:
            step_serp.run('job', '検索語')
        fetch.assert_called_once_with('検索語')
        saved = json.loads(save.call_args.kwargs['content_text'])
        self.assertEqual(saved['people_also_ask'], [])
        self.assertEqual(saved['source'], source)
        self.assertEqual(saved['organic_results'][0]['position'], 1)


if __name__ == '__main__':
    unittest.main()
