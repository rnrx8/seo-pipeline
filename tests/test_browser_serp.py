import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from pipeline.browser_serp import authenticate_owner, bind_snapshot, BrowserAuthorizationError
from pipeline.serp_sources import fetch_serp, SerpConfigurationError, SerpQualityError
from pipeline import step_serp


def snapshot():
    return {'query': '比較 検索', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'search_url': 'https://www.google.co.jp/search?' + urlencode({'q': '比較 検索', 'hl': 'ja', 'gl': 'jp', 'pws': '0'}),
            'organic_results': [{'title': '検索結果' + str(i), 'link': f'https://example{i}.jp/path'} for i in range(3)]}


class BrowserSerpTests(unittest.TestCase):
    def test_browser_mode_cannot_silently_fallback_even_with_provider_keys(self):
        with patch.dict(os.environ, {'SERP_PROVIDER': 'browser', 'SERPER_API_KEY': 'secret'}), patch('pipeline.serp_sources.requests.post') as post:
            with self.assertRaises(SerpConfigurationError): fetch_serp('比較 検索')
            post.assert_not_called()

    def test_user_must_own_job(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://test.supabase.co', 'SUPABASE_KEY': 'secret'}), patch('pipeline.browser_serp.requests.get', return_value=Mock(ok=True, json=lambda: {'id': 'other-user'})):
            with self.assertRaises(BrowserAuthorizationError): authenticate_owner('Bearer test', 'owner')
        with self.assertRaises(BrowserAuthorizationError): authenticate_owner(None, 'owner')

    def test_snapshot_bound_to_exact_job_and_original_query(self):
        job = {'id': 'job-one', 'main_keyword': '比較 検索'}
        supplied = {**snapshot(), 'job_id': 'different-job', 'source': {'provider': 'fake'}}
        bound = bind_snapshot(supplied, job, '比較 検索')
        self.assertEqual(bound['job_id'], job['id'])
        self.assertNotIn('source', bound)
        with self.assertRaises(SerpQualityError): bind_snapshot(supplied, job, '違う検索語')
        for suffix in ['&start=10', '&tbs=li:1', '&tbm=isch']:
            bad = {**snapshot(), 'search_url': snapshot()['search_url'] + suffix}
            with self.assertRaises(SerpQualityError): bind_snapshot(bad, job, '比較 検索')

    def test_captured_urls_order_titles_reach_saved_serp_without_api(self):
        bound = bind_snapshot(snapshot(), {'id': 'job', 'main_keyword': '比較 検索'}, '比較 検索')
        with patch.object(step_serp, 'get_optional_artifact', return_value={'content_text': json.dumps(bound)}), patch.object(step_serp, 'fetch_serp') as provider, patch.object(step_serp, '_fetch_headings', return_value={'fetch_status': 'success'}), patch.object(step_serp, 'upsert_artifact', return_value={'id': 'artifact'}) as save:
            step_serp.run('job', '比較 検索')
        provider.assert_not_called()
        saved = json.loads(save.call_args.kwargs['content_text'])
        self.assertEqual([(r['title'], r['link']) for r in saved['organic_results']], [(r['title'], r['link']) for r in bound['organic_results']])
        self.assertEqual(saved['source']['provider'], 'browser_verified')
        self.assertEqual([r['position'] for r in saved['organic_results']], [1,2,3])

    def test_api_rejects_missing_snapshot_before_scheduling(self):
        import api_server
        from fastapi import BackgroundTasks, HTTPException
        tasks = BackgroundTasks()
        with patch.dict(os.environ, {'SERP_PROVIDER': 'browser'}):
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(api_server.generate(api_server.GenerateRequest(keyword='比較 検索', job_id='job'), tasks, authorization=None))
            self.assertEqual(exc.exception.status_code, 422)
            self.assertFalse(tasks.tasks)

    def test_api_rejects_wrong_owner_before_writing_and_scheduling(self):
        import api_server
        from fastapi import BackgroundTasks, HTTPException
        tasks = BackgroundTasks()
        with patch.object(api_server, 'get_job', return_value={'id':'job','tenant_id':'owner','main_keyword':'比較 検索','status':'queued'}), patch('pipeline.browser_serp.authenticate_owner', side_effect=BrowserAuthorizationError('denied')), patch.object(api_server,'upsert_artifact') as save:
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(api_server.generate(api_server.GenerateRequest(keyword='比較 検索',job_id='job',browser_serp=snapshot()),tasks,authorization='Bearer wrong'))
            self.assertEqual(exc.exception.status_code,403)
            save.assert_not_called(); self.assertFalse(tasks.tasks)

class PublicFetchTests(unittest.TestCase):
    def test_private_dns_target_is_rejected(self):
        from pipeline.public_fetch import require_public_destination
        with patch('pipeline.public_fetch.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(SerpQualityError): require_public_destination('https://example.jp/')

    def test_redirect_to_metadata_ip_is_never_fetched(self):
        from pipeline.public_fetch import get_public_page
        redirect = Mock(status_code=302, headers={'Location':'http://169.254.169.254/latest/meta-data/'})
        session = Mock(); session.get.return_value=redirect
        with patch('pipeline.public_fetch.requests.Session') as session_type, patch('pipeline.public_fetch.socket.getaddrinfo', return_value=[(2,1,6,'',('93.184.216.34',443))]):
            session_type.return_value.__enter__.return_value=session
            with self.assertRaises(SerpQualityError): get_public_page('https://example.jp/',headers={},timeout=5)
            self.assertEqual(session.get.call_count,1)
