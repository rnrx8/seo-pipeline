"""Exercise real research consumers; mocking whole stages hid the regression."""
import copy
import json
import socket
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from pipeline import (db, research_requirements as req, quality_warnings as warnings,
                      step_outline, step_article, step_service_map, step_research_guard,
                      step_final_validate, editorial_blueprint as bp)
from pipeline.content_quality import ContentQualityError, digest, source_evidence
from pipeline.step_plan import research_resume_stage
from test_editorial_blueprint import fixture
from test_reader_value_flow import response


class PartialResearchDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.plan, pages, self.matrix, self.ctx, self.value = fixture()
        self.pages=pages
        self.plan['policy_sha256'] = req.plan_policy()
        self.matrix.update(valid=False, attempt=3, coverage_sufficient=False,
                           coverage_reason='選び分けの説明に改善余地がある')
        self.store = {
            'research_plan': {'content_text': json.dumps(self.plan, ensure_ascii=False)},
            'search_intent': {'content_text': '分析', 'meta': {'reader_needs_version': 'current'}},
            'serp': {'content_text': '{}'},
            'fact_sheet': {'content_text': req.accepted_facts(self.matrix, self.plan)},
            'fresh_sources': {'content_text': json.dumps(pages, ensure_ascii=False)},
            'content_contract': {'content_text': '{"required_sections":[]}'},
        }
        self.matrix.update(policy_sha256=req.matrix_policy(),
            plan_sha256=digest(json.dumps(self.plan, ensure_ascii=False, sort_keys=True)),
            sources_sha256=digest(source_evidence(self.store['fresh_sources'])),
            attempts_sha256=digest(self.store['fresh_sources']['content_text']),
            facts_sha256=digest(self.store['fact_sheet']['content_text']))
        self.put_matrix()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')))
        self.stack.enter_context(patch.object(req, 'load_plan', return_value=self.plan))
        self.stack.enter_context(patch('pipeline.generation_context.authoring_intent', return_value='reader design'))
        self.stack.enter_context(patch('pipeline.db.get_user_email', return_value='nishi.aworks@gmail.com'))
        self.stack.enter_context(patch('pipeline.db.get_learned_style_rules', return_value=[]))
        self.stack.enter_context(patch('pipeline.outline_completion.get_optional_artifact', return_value=None))
        self.stack.enter_context(patch('pipeline.step_article.time.sleep'))
        self.job = {'id': 'job', 'main_keyword': '条件'}
        for module in (db, req, step_outline, step_article, step_service_map,
                       step_research_guard, step_final_validate):
            for name, fn in (('get_artifact', self.read),
                             ('get_optional_artifact', lambda j, k: copy.deepcopy(self.store.get(k))),
                             ('upsert_artifact', self.save), ('get_job', lambda *a: self.job)):
                if hasattr(module, name):
                    self.stack.enter_context(patch.object(module, name, side_effect=fn))

    def read(self, job_id, key):
        if key not in self.store: raise ValueError('Artifact not found: ' + key)
        return copy.deepcopy(self.store[key])

    def save(self, **kw):
        self.store[kw['step']] = {'id': 'saved', **copy.deepcopy(kw)}
        return self.store[kw['step']]

    def put_matrix(self):
        self.store['research_matrix'] = {'content_text': json.dumps(self.matrix, ensure_ascii=False)}

    def test_failed_review_reaches_real_outline_guard_service_map_writer_and_final_save(self):
        original = copy.deepcopy(self.store['research_matrix'])
        with patch.object(step_outline.anthropic, 'Anthropic'), \
             patch.object(step_outline, 'create_with_retry', return_value=response(self.value)) as send:
            outline = warnings.scoped(step_outline.run, 'outline')('job', '条件')
        self.assertEqual(send.call_count, 1)
        bp.require_projection(outline, self.ctx)
        readiness = warnings.scoped(step_research_guard.run, 'research_validation')('job', '条件')
        self.assertTrue(json.loads(readiness['content_text'])['ready_for_draft'])
        warnings.scoped(step_service_map.run, 'service_map')('job', '条件')
        lead = '見ることと送ることでは、必要な利用条件が異なります。用途に合うプランを確認しましょう。'
        body = '# 閲覧と送信の利用条件\n\n' + lead + '\n\n## 閲覧と送信の利用条件\n\n' + (
            '閲覧と送信では必要なプランが異なります。何をしたいかを決めてから、適用されるプランを確認します。\n\n' * 10)
        body += getattr(self, 'additional_body', '')
        with patch.object(step_article, '_call', return_value=(body, 1, 2)) as writer, \
             patch.object(step_article, 'create_with_retry', return_value=response({'lead': lead})):
            article = warnings.scoped(step_article.run, 'article')('job', '条件')
        self.assertEqual(writer.call_count, 1)
        if hasattr(self, 'expected_writer_terms'):
            sent=json.dumps(writer.call_args.args[1],ensure_ascii=False)
            for term in self.expected_writer_terms:self.assertIn(term,sent)
            self.assertNotIn('未確認の地域検索',sent)
        self.assertIn('## 閲覧と送信の利用条件', article['content_text'])
        final = warnings.scoped(step_final_validate.run, 'final_structure_validation')('job', '条件')
        self.assertFalse(json.loads(final['content_text'])['valid'])
        self.assertIn('article', self.store)
        self.assertFalse(self.store['article']['meta']['content_audited'])
        self.assertFalse(self.store['article']['meta']['publication_approved'])
        self.assertTrue(self.store['article']['meta']['completion_with_warnings'])
        self.assertEqual(self.store['research_matrix'], original)

    def test_supported_parts_reach_native_outline_and_body_inputs_with_an_unknown_sibling(self):
        item=self.matrix['items'][0]
        unknown=copy.deepcopy(item['claims'][0])
        unknown.update(text='未確認の地域検索',operation='地域検索',disposition='withhold',evidence_indices=[])
        item['claims'].append(unknown);item['additional_sources_needed']=True
        req.validate_matrix(self.matrix,self.plan,self.pages)
        self.assertFalse(item['verified'])
        self.store['fact_sheet']['content_text']=req.accepted_facts(self.matrix,self.plan)
        self.matrix['facts_sha256']=digest(self.store['fact_sheet']['content_text'])
        self.put_matrix()
        self.expected_writer_terms=['男性スタンダードは閲覧できます。','送信はプレミアムのみです。','q1.c01','q1.c02']
        self.test_failed_review_reaches_real_outline_guard_service_map_writer_and_final_save()

    def test_stale_partial_claims_cannot_survive_material_currentness_checks(self):
        from pipeline.generation_context import bundle_data
        item=self.matrix['items'][0]
        unknown=copy.deepcopy(item['claims'][0]);unknown.update(disposition='withhold',evidence_indices=[])
        item['claims'].append(unknown);req.validate_matrix(self.matrix,self.plan,self.pages)
        self.matrix['facts_sha256']='source-material-changed';self.put_matrix()
        original=copy.deepcopy(self.store['research_matrix'])
        value=warnings.scoped(lambda j:bundle_data(self.plan,req.require_matrix(j),quotes=True),'article')('job')
        self.assertEqual(value['decisions'],[]);self.assertEqual(value['sources'],{})
        self.assertEqual(self.store['research_matrix'],original)

    def test_initial_and_strict_recovery_writers_receive_identical_supported_claims(self):
        from pipeline.generation_context import generation_evidence
        item=self.matrix['items'][0]
        unknown=copy.deepcopy(item['claims'][0])
        unknown.update(text='未確認の地域検索',operation='地域検索',disposition='withhold',evidence_indices=[])
        item['claims'].append(unknown);item['additional_sources_needed']=True
        req.validate_matrix(self.matrix,self.plan,self.pages)
        self.store['fact_sheet']['content_text']=req.accepted_facts(self.matrix,self.plan)
        self.matrix['facts_sha256']=digest(self.store['fact_sheet']['content_text']);self.put_matrix()
        original=copy.deepcopy(self.store)
        normal=warnings.scoped(lambda j:generation_evidence(j,'',''),'article')('job')
        with warnings.strict_checks():recovery=generation_evidence('job','','')
        self.assertEqual(normal,recovery)
        self.assertIn('q1.c01',recovery);self.assertIn('q1.c02',recovery)
        self.assertNotIn('未確認の地域検索',recovery)
        self.assertEqual(self.store['research_matrix'],original['research_matrix'])
        with self.assertRaises(ContentQualityError):req._require_matrix_strict('job')

    def test_authoring_data_never_reuses_pending_stale_or_mismatched_material(self):
        for change in ({'status':'running'},{'attempts_sha256':'changed'},
                       {'facts_sha256':'changed'},{'sources_sha256':'changed'},
                       {'policy_sha256':'changed'},{'items':[]}):
            with self.subTest(change=change):
                original=copy.deepcopy(self.matrix)
                self.matrix.update(change);self.put_matrix()
                with self.assertRaises(ContentQualityError):req.require_authoring_matrix('job')
                self.matrix=original;self.put_matrix()

    def test_resume_reuses_completed_failed_review_without_research_or_plan_resend(self):
        keys = ['research_plan', 'fact_sheet', 'research_completeness', 'outline', 'article']
        load = lambda j, k: self.store.get(k)
        self.assertTrue(req.matrix_ready_for_draft('job', self.matrix))
        self.assertEqual(research_resume_stage('job', 'outline', keys, load), 'outline')
        with self.assertRaises(ContentQualityError): req._require_matrix_strict('job')

    def test_pending_or_changed_material_is_distinct_from_editorial_rejection(self):
        for change in ({'status': 'running'}, {'items': []}, {'attempts_sha256': 'changed'},
                       {'facts_sha256': 'changed'}, {'policy_sha256': 'changed'}):
            with self.subTest(change=change):
                self.assertFalse(req.matrix_ready_for_draft('job', {**self.matrix, **change}))

    def test_partial_delivery_never_promotes_unverified_or_stale_evidence(self):
        from pipeline.generation_context import bundle_data
        original = copy.deepcopy(self.store['research_matrix'])
        self.matrix['items'][0]['verified'] = False
        self.put_matrix()
        value = warnings.scoped(lambda j: bundle_data(self.plan, req.require_matrix(j), quotes=True), 'outline')('job')
        self.assertEqual(value['decisions'], [])
        self.assertEqual(value['sources'], {})
        self.assertNotEqual(self.store['research_matrix'], original)
        self.assertFalse(json.loads(self.store['research_matrix']['content_text'])['valid'])
        self.matrix['items'][0]['verified'] = True
        self.matrix['facts_sha256'] = 'stale'
        self.put_matrix()
        value = warnings.scoped(lambda j: bundle_data(self.plan, req.require_matrix(j), quotes=True), 'article')('job')
        self.assertEqual(value['decisions'], [])
        self.assertTrue(json.loads(self.store['research_matrix']['content_text'])['items'][0]['verified'])


if __name__ == '__main__': unittest.main()
