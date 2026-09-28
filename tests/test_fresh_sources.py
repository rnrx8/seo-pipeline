import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline import fresh_sources as fs
from pipeline.step_fact_sheet import _downgrade_incomplete_confirmations

URL = 'https://official.example/pricing'
BODY = '<html><title>料金</title><main>' + ('現在の料金は月額2,000円です。' * 10) + '</main><script>月額1,000円</script></html>'


def response(body=BODY):
    return SimpleNamespace(url=URL, status_code=200, content=body.encode(), headers={'Content-Type': 'text/html'}, encoding='utf-8')


class FreshSourcesTests(unittest.TestCase):
    def test_http_charset_takes_priority_over_conflicting_html_meta(self):
        html = '<meta charset="utf-8"><title>料金</title><main>' + '男性の有料会員は月額料金がかかります。' * 12 + '</main>'
        page = SimpleNamespace(url=URL, status_code=200, content=html.encode('shift_jis'),
                               headers={'Content-Type': 'text/html; charset=Shift-JIS'})
        with patch.object(fs, 'get_public_page', return_value=page):
            result = fs.FreshSources({}, []).fetch(URL)
        self.assertEqual(result['status'], 'success')
        self.assertIn('男性の有料会員', result['text'])
        self.assertNotIn('\ufffd', result['text'])

    def test_garbled_text_is_not_successful_evidence(self):
        with patch.object(fs, 'get_public_page', return_value=response('<main>' + '\ufffd' * 200 + '</main>')):
            result = fs.FreshSources({}, []).fetch(URL)
        self.assertEqual(result['status'], 'failed')

    def test_selected_settings_urls_are_extracted_before_model_decisions(self):
        job = {'tenant_id': 'owner', 'service_id': 's', 'cta_id': 'c', 'category': 'cat', 'must_reference_urls': 'https://required.example/'}
        service = {'tenant_id': 'owner', 'url': URL, 'raw_content': '詳細 https://official.example/features\n', 'selling_points': ['料金 ' + URL]}
        with patch.object(fs.db, 'get_service_by_id', return_value=service), \
             patch.object(fs.db, 'get_cta_by_id', return_value={'tenant_id': 'owner', 'url': 'https://cta.example/'}), \
             patch.object(fs.db, 'get_company_settings', return_value=[{'recommend_level': 1, 'notes': 'https://company.example/'}, {'recommend_level': 0, 'notes': 'https://excluded.example/'}]), \
             patch.object(fs, 'get_public_page', side_effect=lambda url, **kw: response()) as get:
            fresh = fs.FreshSources(job, fs.load_settings(job, [{'content_text': '資料 https://source.example/'}]))
            fresh.prefetch()
        self.assertEqual({c.args[0] for c in get.call_args_list}, {URL, 'https://official.example/features', 'https://required.example', 'https://cta.example', 'https://company.example', 'https://source.example'})

    def test_selected_setting_from_another_tenant_is_rejected(self):
        with patch.object(fs.db, 'get_service_by_id', return_value={'tenant_id': 'other', 'url': URL}):
            with self.assertRaises(ValueError): fs.load_settings({'tenant_id': 'owner', 'service_id': 's'}, [])

    def test_every_run_fetches_again_and_old_quote_cannot_be_confirmed(self):
        with patch.object(fs, 'get_public_page', side_effect=[response(BODY.replace('2,000', '1,000')), response()]) as get:
            old, new = fs.FreshSources({}, []), fs.FreshSources({}, [])
            old.fetch(URL); new.fetch(URL)
        self.assertEqual(get.call_count, 2)
        self.assertIn('no-cache', get.call_args.kwargs['headers']['Cache-Control'])
        self.assertIn('月額2,000円', new.pages[URL]['text'])
        self.assertNotIn('月額1,000円', new.pages[URL]['text'])
        for price, count in [('1,000', 1), ('2,000', 0)]:
            fact = f'料金は月額{price}円。出典：{URL}｜確認日：2026-09-28｜確認箇所：「現在の料金は月額{price}円です。」｜[confirmed]'
            self.assertEqual(_downgrade_incomplete_confirmations(fact, searched_urls=set(), checked_on='2026-09-28', fresh=new)[1], count)

    def test_failed_or_blocked_pages_never_supply_evidence(self):
        fresh = fs.FreshSources({'never_reference_urls': URL}, [])
        with patch.object(fs, 'get_public_page') as get:
            self.assertEqual(fresh.fetch(URL)['status'], 'failed')
            get.assert_not_called()
        with patch.object(fs, 'get_public_page', return_value=response('<title>Just a moment...</title>' + BODY)):
            self.assertEqual(fs.FreshSources({}, []).fetch(URL)['status'], 'failed')
        with patch.object(fs, 'get_public_page', return_value=response('<html><div id="root"></div></html>')):
            self.assertEqual(fs.FreshSources({}, []).fetch(URL)['status'], 'failed')
        with self.assertRaises(ValueError): fresh.check_allowed(URL + '/detail')

    def test_oversized_settings_are_not_silently_skipped(self):
        fresh = fs.FreshSources({}, [{'kind':'test', 'data':' '.join(f'https://example.com/{i}' for i in range(41))}])
        with self.assertRaises(ValueError): fresh.prefetch()

    def test_fetch_loop_resumes_pause_and_returns_real_tool_result(self):
        usage = lambda: SimpleNamespace(input_tokens=1, output_tokens=2)
        pause = SimpleNamespace(content=[], usage=usage(), stop_reason='pause_turn')
        fetch = SimpleNamespace(content=[SimpleNamespace(type='tool_use', id='fetch1', name='fetch_current_page', input={'url': URL})], usage=usage(), stop_reason='tool_use')
        end = SimpleNamespace(content=[SimpleNamespace(type='text', text='finished')], usage=usage(), stop_reason='end_turn')
        create = Mock(side_effect=[pause, fetch, end])
        fresh = fs.FreshSources({}, [])
        with patch.object(fs, 'get_public_page', return_value=response()):
            resp, text, queries, observed = fs.run_with_fetch(None, create=create, model='test', max_tokens=100, system='', prompt='', search_tool={}, fresh=fresh)
        self.assertEqual(text, 'finished')
        self.assertEqual(resp.usage.input_tokens, 3)
        result = create.call_args.kwargs['messages'][-1]['content'][0]
        self.assertEqual(result['tool_use_id'], 'fetch1')
        self.assertIn('月額2,000円', result['content'])
        self.assertFalse(result['is_error'])

    def test_model_claimed_confirmation_is_fetched_without_tool_choice(self):
        fresh = fs.FreshSources({}, [])
        with patch.object(fs, 'get_public_page', return_value=response()) as get:
            fresh.fetch_confirmed_citations(f'料金：{URL}｜確認箇所：「現在の料金は月額2,000円です。」｜[confirmed]')
        get.assert_called_once()
        self.assertEqual(fresh.pages[URL]['status'], 'success')

    def test_each_quote_must_match_and_review_cannot_confirm_failed_evidence(self):
        from pipeline.step_fact_review import _require_direct_evidence
        fresh = fs.FreshSources({}, [])
        with patch.object(fs, 'get_public_page', return_value=response()): fresh.fetch(URL)
        report = f'### Claim 1\n- 判定: VERIFIED_T1\n- 根拠: {URL}\n- 確認箇所: 「現在の料金は月額2,000円です。」'
        _require_direct_evidence(report, fresh)
        for invalid in (report.replace('2,000円です。', '1,000円です。'), report + '「現在の料金は月額1,000円です。」'):
            with self.assertRaises(ValueError): _require_direct_evidence(invalid, fresh)

    def test_japanese_url_separator_and_labeled_quote_from_real_response(self):
        from pipeline.step_fact_review import _require_direct_evidence
        fresh = fs.FreshSources({}, [])
        with patch.object(fs, 'get_public_page', return_value=response()): fresh.fetch(URL)
        report = f'### Claim 1\n- **判定:** CORRECTED\n- 根拠URL: https://other.example、{URL}\n- **確認箇所（料金）:** 「現在の料金は月額2,000円です。」'
        _require_direct_evidence(report, fresh)
        self.assertEqual(fs.extract_urls(f'https://other.example、{URL}'), ['https://other.example', URL])

    def test_missing_review_output_blocks_get_one_repair(self):
        from pipeline import step_fact_review as review
        first = SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=4))
        second = SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=6))
        complete = '===ARTICLE_START===\n記事\n===ARTICLE_END===\n===FINAL_AUDIT_START===\nPASS\n===FINAL_AUDIT_END==='
        with patch.object(review, 'run_with_fetch', side_effect=[(first, 'PASS only', [], []), (second, complete, [], [])]) as run:
            resp, raw, _ = review._run_search_pass(None, system=review.AUDIT_SYSTEM_PROMPT, prompt='', tool={}, fresh=fs.FreshSources({}, []))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(raw, complete)
        self.assertEqual(resp.usage.input_tokens, 8)
        self.assertEqual(resp.usage.output_tokens, 10)

    def test_redirect_blocklist_and_stream_size_limit(self):
        from pipeline.public_fetch import get_public_page
        redirect = Mock(status_code=302, headers={'Location': URL})
        session = Mock(); session.get.return_value = redirect
        with patch('pipeline.public_fetch.requests.Session') as factory, patch('pipeline.public_fetch.require_public_destination'):
            factory.return_value.__enter__.return_value = session
            fresh = fs.FreshSources({'never_reference_urls': URL}, [])
            with self.assertRaises(ValueError): get_public_page('https://other.example', headers={}, timeout=2, max_bytes=10, destination_check=fresh.check_allowed)
            self.assertEqual(session.get.call_count, 1)
        big = Mock(status_code=200); big.iter_content.return_value = [b'01234567890']
        session.get.return_value = big
        with patch('pipeline.public_fetch.requests.Session') as factory, patch('pipeline.public_fetch.require_public_destination'):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(ValueError): get_public_page(URL, headers={}, timeout=2, max_bytes=10)
            big.close.assert_called_once()


if __name__ == '__main__': unittest.main()
