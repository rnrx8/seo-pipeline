import json
import os
import unittest
import requests
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from pipeline.ai import create_with_retry, get_step_config, message_text, validate_model_credentials
from pipeline.content_quality import (ContentQualityError, CHECKS, EDITORIAL_CHECKS,
    audit, audit_output_config, final_review_requirements, snapshot)
from pipeline.openai_review import create_review_response
from pipeline.step_plan import build_step_plan


class ModelRoutingTests(unittest.TestCase):
    def request(self):
        return dict(model='gpt-6-astra', max_tokens=7000, system='audit',
                    messages=[{'role':'user','content':'sample'}],
                    output_config=audit_output_config(CHECKS))

    def response(self, **overrides):
        return {'status':'completed', 'model':'gpt-6-astra',
                'output':[{'type':'reasoning'}, {'type':'message', 'content':[{'type':'output_text','text':'{}'}]}],
                'usage':{'input_tokens':10,'output_tokens':20}, **overrides}

    def http_response(self, status, data):
        response = Mock(status_code=status)
        response.json.return_value = data
        response.iter_lines.return_value = [
            b'event: response.completed',
            ('data: ' + json.dumps({'type':'response.completed','response':data})).encode(), b'']
        return response

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only', 'ARTICLE_REVIEW_PROVIDER':'astra'})
    def test_responses_schema_and_usage(self):
        response=self.http_response(200, self.response())
        with patch('pipeline.openai_review.requests.post',return_value=response) as post:
            msg=create_review_response(**self.request())
        payload=post.call_args.kwargs['json']
        self.assertFalse(payload['store'])
        self.assertTrue(payload['stream'])
        self.assertTrue(post.call_args.kwargs['stream'])
        self.assertEqual(payload['reasoning'],{'effort':'high'})
        self.assertTrue(payload['text']['format']['strict'])
        self.assertNotIn('temperature',payload)
        self.assertGreaterEqual(payload['max_output_tokens'],16000)
        self.assertEqual(message_text(msg),'{}')
        self.assertEqual(msg.usage.output_tokens,20)

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_incomplete_refusal_empty_and_bad_http_fail_closed(self):
        cases=[(200,self.response(status='incomplete')),
               (200,self.response(output=[])),
               (200,self.response(output=[{'type':'message','content':[{'type':'refusal'}]}])),
               (401,{'secret':'must not appear'})]
        for status,data in cases:
            response=self.http_response(status, data)
            with patch('pipeline.openai_review.requests.post',return_value=response), self.assertRaises(ContentQualityError) as caught:
                create_review_response(**self.request())
            self.assertNotIn('test-only',str(caught.exception))
            self.assertNotIn('must not appear',str(caught.exception))

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_stream_disconnect_and_partial_output_never_complete_or_retry(self):
        response = self.http_response(200, self.response())
        for lines in ([b'data: {"type":"response.output_text.delta","delta":"{}"}', b''],
                      [b'data: {"type":"response.failed","message":"must not appear"}', b''],
                      [b'data: malformed', b'']):
            response.iter_lines.return_value = lines
            with patch('pipeline.openai_review.requests.post',return_value=response) as post, self.assertRaises(ContentQualityError) as caught:
                create_review_response(**self.request())
            self.assertEqual(post.call_count, 1)
            self.assertNotIn('must not appear', str(caught.exception))
            response.close.assert_called()
        response.iter_lines.side_effect = requests.exceptions.ReadTimeout('test-only')
        with patch('pipeline.openai_review.requests.post',return_value=response), self.assertRaises(ContentQualityError) as caught:
            create_review_response(**self.request())
        self.assertIn('ReadTimeout',str(caught.exception))
        self.assertNotIn('test-only',str(caught.exception))

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_stream_wait_and_size_are_bounded(self):
        response = self.http_response(200, self.response())
        with patch('pipeline.openai_review.requests.post',return_value=response), \
             patch('pipeline.openai_review.time.monotonic',side_effect=[0,1201]), self.assertRaises(ContentQualityError):
            create_review_response(**self.request())
        response.iter_lines.return_value = [b'data: ' + b'x' * 8_000_001]
        with patch('pipeline.openai_review.requests.post',return_value=response), self.assertRaises(ContentQualityError):
            create_review_response(**self.request())

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_quota_failure_not_retried(self):
        response=Mock(status_code=429);response.json.return_value={'error':{'code':'insufficient_quota'}}
        with patch('pipeline.openai_review.requests.post',return_value=response) as post, self.assertRaises(ContentQualityError):
            create_review_response(**self.request())
        self.assertEqual(post.call_count,1)

    @patch.dict(os.environ, {'OPENAI_API_KEY':'test-only'})
    def test_rate_limit_retry_bounded(self):
        response=Mock(status_code=429);response.json.return_value={'error':{'code':'rate_limit_exceeded'}}
        with patch('pipeline.openai_review.requests.post',return_value=response) as post, \
             patch('pipeline.openai_review.time.sleep'), self.assertRaises(ContentQualityError):
            create_review_response(**self.request())
        self.assertEqual(post.call_count,3)

    @patch.dict(os.environ, {'OPENAI_API_KEY':'', 'ARTICLE_REVIEW_PROVIDER':'astra'})
    def test_missing_key_stops_before_paid_generation(self):
        with self.assertRaises(ContentQualityError):validate_model_credentials({})
        validate_model_credentials({'delivery_type':'outline_only'})
        with patch('pipeline.openai_review.requests.post') as post, self.assertRaises(ContentQualityError):
            create_review_response(**self.request())
        post.assert_not_called()

    def test_opus_thinking_blocks_and_budget(self):
        response=NS(content=[NS(type='thinking'),NS(type='text',text='本文')])
        client=Mock();client.messages.stream.return_value.__enter__=Mock(return_value=NS(get_final_message=lambda:response))
        client.messages.stream.return_value.__exit__=Mock(return_value=False)
        result=create_with_retry(client,model='claude-opus-5-5',max_tokens=10000,messages=[],system='test')
        self.assertEqual(message_text(result),'本文')
        self.assertEqual(client.messages.stream.call_args.kwargs['max_tokens'],18000)
        self.assertEqual(client.messages.stream.call_args.kwargs['output_config']['effort'],'medium')

    def test_astra_does_not_call_anthropic(self):
        client=Mock()
        with patch('pipeline.openai_review.create_review_response',return_value='ok'):
            self.assertEqual(create_with_retry(client,**self.request()),'ok')
        client.messages.stream.assert_not_called()

    def test_rollback_keeps_sonnet_review_and_invalidates_astra_snapshot(self):
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'astra'}):
            first=snapshot('a','f','o',{}, {})
            self.assertNotIn('review',[k for k,_ in build_step_plan({})])
        with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'sonnet','ARTICLE_GENERATION_MODEL':'claude-opus-4-8'}):
            self.assertIn('review',[k for k,_ in build_step_plan({})])
            self.assertEqual(get_step_config('article')[0],'claude-opus-4-8')
            self.assertEqual(get_step_config('content_audit')[0],'claude-sonnet-4-6')
            self.assertNotEqual(first,snapshot('a','f','o',{}, {}))

    @patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'astra'})
    def test_learned_rules_reach_final_requirements_and_snapshot(self):
        with patch('pipeline.db.get_learned_style_rules',return_value=[{'rule_text':'弊社を当社に統一'}]):
            req=final_review_requirements({'tenant_id':'test'},'keyword')
        self.assertEqual(req['learned_style_rules'],['弊社を当社に統一'])
        self.assertIn('H2直下',req['editorial_rules'])
        self.assertNotEqual(snapshot('a','f','o',{},req),snapshot('a','f','o',{}, {}))

    @patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'astra'})
    def test_research_stays_sonnet_and_article_uses_astra(self):
        def reply(*args,**kwargs):
            editorial=kwargs['output_config']['format']['schema']['properties']['checks']['items']['properties'].get('affected_blocks')
            keys=EDITORIAL_CHECKS if editorial else CHECKS
            checks=[{'key':k,'reason':'checked','status':'pass',**({'affected_blocks':[]} if editorial else {})} for k in keys]
            return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps({'checks':checks}))],usage=NS(input_tokens=1,output_tokens=1))
        with patch('pipeline.content_quality.create_with_retry',side_effect=reply) as call:
            audit(None,stage='research',text='text',facts='',outline='',contract={},requirements={})
            self.assertEqual(call.call_args.kwargs['model'],'claude-sonnet-4-6')
            result=audit(None,stage='article',text='text',facts='',outline='',contract={},requirements={'learned_style_rules':['rule']})
            self.assertTrue(result['valid'])
            self.assertEqual(result['model'],'gpt-6-astra')
            self.assertEqual(call.call_args.kwargs['model'],'gpt-6-astra')
            self.assertIn('rule',call.call_args.kwargs['messages'][0]['content'])


if __name__=='__main__':unittest.main()
