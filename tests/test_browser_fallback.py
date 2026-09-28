import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from pipeline import step_serp, fresh_sources, browser_fetch
from pipeline.fetch_status import classify_failure, unverified_message

URL='https://example.com/dynamic'
SHELL=b'<html><title>Service</title><main id="app"></main><script src="/app.js"></script></html>'
RENDERED={'status':'success','text':'今回のサービス情報です。'*20,'title':'Service','fetch_method':'browser','word_count':240,'headings':[{'level':'h2','text':'料金'}],'fetched_at':'2026-09-28T00:00:00Z','final_url':URL,'sha256':'test'}

def response(content=SHELL):
    return SimpleNamespace(content=content,raise_for_status=lambda: None,url=URL,status_code=200,headers={'Content-Type':'text/html'})


class BrowserFallbackTests(unittest.TestCase):
    def test_normal_mode_reports_unverified_without_launching_browser(self):
        with patch.object(step_serp,'get_public_page',return_value=response()), patch.object(step_serp,'fetch_rendered_page') as render:
            result=step_serp._fetch_headings({'link':URL})
        render.assert_not_called()
        self.assertEqual(result['fetch_status'],'failed')
        self.assertEqual(result['failure_code'],'dynamic_content')
        self.assertIn('99％',result['failure_message'])

    def test_high_accuracy_recovers_dynamic_page_headings(self):
        with patch.object(step_serp,'get_public_page',return_value=response()), patch.object(step_serp,'fetch_rendered_page',return_value=RENDERED) as render:
            result=step_serp._fetch_headings({'link':URL}, True)
        render.assert_called_once_with(URL, ())
        self.assertEqual(result['fetch_status'],'success')
        self.assertEqual(result['fetch_method'],'browser')
        self.assertEqual(result['heading_count'],1)

    def test_direct_success_does_not_render_even_in_high_accuracy(self):
        with patch.object(step_serp,'get_public_page',return_value=response(b'<main>'+b'content '*50+b'<h2>Plans</h2></main>')), patch.object(step_serp,'fetch_rendered_page') as render:
            result=step_serp._fetch_headings({'link':URL},True)
        render.assert_not_called()
        self.assertEqual(result['fetch_method'],'http')

    def test_browser_failure_stays_unverified_without_upgrade_loop(self):
        with patch.object(step_serp,'get_public_page',side_effect=requests.Timeout()), patch.object(step_serp,'fetch_rendered_page',return_value={'status':'failed','reason_code':'browser_unverified'}):
            result=step_serp._fetch_headings({'link':URL},True)
        self.assertEqual(result['fetch_status'],'failed')
        self.assertTrue(result['browser_attempted'])
        self.assertNotIn('試せます',result['failure_message'])
        self.assertNotIn('JavaScript',result['failure_message'])

    def test_service_sources_use_same_mode_gate_and_preserve_evidence(self):
        for high in [False,True]:
            with self.subTest(high=high), patch.object(fresh_sources,'get_public_page',return_value=response()), patch.object(fresh_sources,'fetch_rendered_page',return_value=RENDERED) as render:
                sources=fresh_sources.FreshSources({'high_accuracy_mode':high},[])
                result=sources.fetch(URL)
                self.assertEqual(render.call_count,int(high))
                self.assertEqual(result['status'],'success' if high else 'failed')
                if high:
                    self.assertEqual(result['fetch_method'],'browser')
                    self.assertTrue(sources.evidence_matches(f'出典: {URL}\n確認箇所:「今回のサービス情報です。」'))

    def test_private_and_forbidden_targets_never_launch_browser(self):
        for url,blocked in [('http://127.0.0.1',[]),(URL,[URL])]:
            with patch.object(browser_fetch,'_worker') as worker:
                result=browser_fetch.fetch_rendered_page(url,blocked)
            worker.assert_not_called()
            self.assertEqual(result['status'],'failed')

    def test_error_categories_do_not_assume_javascript(self):
        self.assertEqual(classify_failure(requests.Timeout()),'timeout')
        for status,expected in [(403,'access_restricted'),(404,'not_found'),(500,'http_error')]:
            exc=requests.HTTPError(response=SimpleNamespace(status_code=status))
            self.assertEqual(classify_failure(exc),expected)
        self.assertNotIn('JavaScript',unverified_message('connection_failed'))

    def test_container_start_resolves_the_runtime_port(self):
        from pipeline import serve
        with patch.dict(os.environ, {'PORT':'9876'}), patch.object(serve.uvicorn, 'run') as run:
            serve.main()
        run.assert_called_once_with('api_server:app', host='0.0.0.0', port=9876)

    def test_child_environment_excludes_credentials(self):
        process=Mock(returncode=0);process.communicate.return_value=(json.dumps({'status':'success'}),'')
        with patch.dict(os.environ,{'SUPABASE_KEY':'secret','ANTHROPIC_API_KEY':'secret'}), patch.object(browser_fetch.subprocess,'Popen',return_value=process) as popen:
            browser_fetch._worker({'probe':True})
        env=popen.call_args.kwargs['env']
        self.assertNotIn('SUPABASE_KEY',env)
        self.assertNotIn('ANTHROPIC_API_KEY',env)

if __name__=='__main__': unittest.main()
