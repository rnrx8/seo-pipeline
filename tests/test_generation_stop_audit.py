"""Reproduce missed pre-body gates through the real delivery context, offline."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import quality_warnings as q, editorial_blueprint as bp
from pipeline import step_article as writer, article_citations as citations
from pipeline.content_quality import ContentQualityError
from test_editorial_blueprint import fixture, artifact


class GenerationStopAuditTests(unittest.TestCase):
    def setUp(self):
        self.network=patch('socket.socket.connect',side_effect=AssertionError('offline only'))
        self.network.start();self.addCleanup(self.network.stop)

    def test_coverage_and_reader_progression_remain_findings_not_generation_gates(self):
        *_,ctx,value=fixture()
        mutations=[
            lambda v:(v['sections'][0].update(research_ids=[]),v['sections'][0]['answer_elements'][0].update(claim_ids=[])),
            lambda v:v['sections'][0].update(decision_ids=[]),
            lambda v:v['sections'][0].update(reader_progression=None),
            lambda v:v['sections'][0]['reader_progression']['after'].update(resolved_questions=[]),
            lambda v:v['sections'][0]['reader_progression']['before'].update(understanding='',open_questions=[]),
        ]
        for change in mutations:
            v=copy.deepcopy(value);change(v)
            with self.subTest(value=v):
                with self.assertRaises(ContentQualityError):bp.validate(v,ctx)
                with patch.object(q,'warn') as warning:
                    saved=q.scoped(lambda *_:artifact(bp.validate(v,ctx),ctx),'outline')('local')
                    q.scoped(lambda *_:bp.require_projection(saved),'article')('local')
                self.assertTrue(warning.called)
                self.assertEqual(saved['meta']['editorial_blueprint'],v)
                self.assertNotIn('publication_approved',saved['meta'])
                with self.assertRaises(ContentQualityError):bp.require_projection(saved)

    def test_unknown_claims_and_corrupted_projections_are_not_accepted_as_evidence(self):
        *_,ctx,value=fixture()
        value['sections'][0]['answer_elements'][0]['claim_ids']=['unknown']
        with patch.object(q,'warn'),self.assertRaises(ContentQualityError):
            q.scoped(lambda *_:bp.validate(value,ctx),'outline')('local')

    def test_embedded_line_breaks_are_normalized_without_rewriting_meaning(self):
        *_,ctx,value=fixture()
        value['sections'][0]['conclusion']='見る用途と送る用途で\n必要プランを分けて判断する'
        value['sections'][0]['reader_progression']['after']['emotion']='まず条件を確認して\r\n選べる'
        original=copy.deepcopy(value)
        result=bp.canonicalize_research_refs(value,ctx)
        self.assertEqual(bp.validate(result,ctx),result)
        self.assertEqual(value,original)
        self.assertEqual(result['sections'][0]['conclusion'],'見る用途と送る用途で 必要プランを分けて判断する')

    def test_stale_service_map_is_rebuilt_once_from_the_blueprint_without_ai(self):
        *_,ctx,value=fixture();outline=artifact(value,ctx)
        old={'service_section_type':'obsolete','cta_after_h2':['previous chapter']}
        expected=bp.placement(outline)
        with patch.object(writer,'upsert_artifact',side_effect=lambda **kw:kw) as save,patch.object(writer,'warn') as warning, \
             patch.object(writer,'create_with_retry',side_effect=AssertionError('no paid map repair')):
            result=q.scoped(lambda *_:writer._current_service_map('local',outline,old),'article')('local')
            self.assertEqual(result,expected)
            self.assertEqual(json.loads(save.call_args_list[0].kwargs['content_text']),old)
            self.assertEqual(json.loads(save.call_args_list[1].kwargs['content_text']),expected)
            self.assertEqual(save.call_args_list[1].kwargs['meta']['model_calls'],0)
            self.assertEqual(writer._current_service_map('local',outline,result),result)
            self.assertEqual(save.call_count,2);warning.assert_called_once()

    def response(self,text,stop='end_turn'):
        return SimpleNamespace(stop_reason=stop,content=[SimpleNamespace(type='text',text=text)],
                               usage=SimpleNamespace(input_tokens=10,output_tokens=20))

    def test_output_limit_preserves_paid_prefix_and_only_requests_its_suffix(self):
        responses=[self.response('## 比較\n\n条件を確かめて','max_tokens'),
                   self.response('選びます。【PART1_END】')]
        with patch.object(writer,'create_with_retry',side_effect=responses) as send, \
             patch.object(writer,'upsert_artifact',side_effect=lambda **kw:kw) as save, \
             patch.object(writer,'warn'),patch.object(writer,'validate_delivery',side_effect=lambda *a,**k:[]), \
             patch.object(writer,'scope_issues',return_value=[]):
            text,ti,to=q.scoped(lambda *_:writer._write_complete_part(None,[{'role':'user','content':'依頼'}],100,
                '### H2：比較',[('比較',1,100)],1,job_id='local'),'article')('local')
        self.assertEqual(text,'## 比較\n\n条件を確かめて選びます。')
        self.assertEqual((ti,to),(20,40));self.assertEqual(send.call_count,2)
        self.assertIn('未執筆部分だけ',send.call_args.kwargs['messages'][-1]['content'])
        raw=[c.kwargs for c in save.call_args_list if '_response_' in c.kwargs['step']]
        self.assertEqual([v['content_text'] for v in raw],[r.content[0].text for r in responses])
        self.assertEqual(raw[0]['meta']['stop_reason'],'max_tokens')

    def test_repeated_output_limit_is_bounded_and_not_relabelled_complete(self):
        response=self.response('## 比較\n\n途中の説明','max_tokens')
        with patch.object(writer,'create_with_retry',return_value=response) as send, \
             patch.object(writer,'upsert_artifact',side_effect=lambda **kw:kw) as save, \
             patch.object(writer,'warn') as warning,patch.object(writer,'validate_delivery',side_effect=lambda *a,**k:[]), \
             patch.object(writer,'scope_issues',return_value=[]):
            text,_,_=q.scoped(lambda *_:writer._write_complete_part(None,[{'role':'user','content':'依頼'}],100,
                '### H2：比較',[('比較',1,100)],1,job_id='local'),'article')('local')
        self.assertEqual(send.call_count,2);self.assertIn('途中の説明',text)
        self.assertFalse(save.call_args.kwargs['meta']['valid'])
        self.assertIn('output_truncated',str(save.call_args.kwargs['meta']['issues']))
        self.assertTrue(warning.called)

    def test_unknown_citation_does_not_stop_prose_or_acquire_a_guessed_url(self):
        registry=citations.build({'ref1':{'url':'https://official.example/a'}})
        raw='## 比較\n\n元の主張。[[cite:unknown]] 有効な出典。[[cite:ref1]]'
        with self.assertRaises(ContentQualityError):citations.render(raw,registry)
        with patch.object(q,'warn') as warning:
            text=q.scoped(lambda *_:citations.render_draft(raw,registry),'article')('local')
        self.assertIn('元の主張。',text);self.assertNotIn('unknown',text)
        self.assertIn('有効な出典。※1',text);self.assertIn('※1: https://official.example/a',text)
        self.assertTrue(warning.called)
        with self.assertRaises(ContentQualityError):citations.render(raw,registry)

    def test_conflicting_citation_identity_is_removed_without_reattribution(self):
        registry=citations.build({'ref1':{'url':'https://official.example/a'}})
        raw='説明。※1\n\n※1: https://different.example/\n\n```text\n[[cite:unknown]]\n```'
        with patch.object(q,'warn'):
            text=q.scoped(lambda *_:citations.render_draft(raw,registry),'article')('local')
        self.assertIn('説明。',text);self.assertNotIn('※1',text);self.assertNotIn('official.example',text)
        self.assertNotIn('different.example',text);self.assertIn('[[cite:unknown]]',text)
