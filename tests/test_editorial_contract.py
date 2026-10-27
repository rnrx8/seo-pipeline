import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pipeline import editorial_contract as ec, initial_authoring as ia, production_brief as pb, db


def fixture():
    job = {'id': 'j', 'tenant_id': 'owner', 'service_id': 's', 'cta_id': 'c',
           'category': 'dating', 'article_purpose': 'cv', 'custom_prompt': '中立な均等推薦は不要'}
    product = {'id': 's', 'tenant_id': 'owner', 'name': '既婚者クラブ', 'category': '既婚者マッチング', 'url': 'https://example.com'}
    material = {'version': pb.PRODUCT_VERSION, 'tenant_id': 'owner', 'service': product, 'materials': []}
    brief = {'version': pb.VERSION, 'keyword': '探し方', 'request': {k: job.get(k) for k in ec.REQUEST_FIELDS},
             'product': product, 'cta': {'name': '登録', 'url': 'https://example.com/register'},
             'registered_companies': [], 'product_input_sha256': pb.fingerprint(material)}
    return job, brief, material, ec.build('j', job, brief, material, [{'rule_text': '普段の言葉で具体的に説明する'}])


class EditorialContractTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_scoped(self):
        job, brief, material, value = fixture()
        brief['product']['name'] = 'changed'
        with ec.use(value):
            self.assertEqual(ia.assignment(brief)['product']['name'], '既婚者クラブ')
            self.assertEqual(pb.prepare('j', '探し方')[0]['product']['name'], '既婚者クラブ')
            self.assertEqual(ec.learned_rules(job)[0]['rule_text'], '普段の言葉で具体的に説明する')
        self.assertIsNone(ec.CURRENT.get())

    def test_company_failure_cannot_remove_selected_product_or_cta(self):
        job, brief, _, _ = fixture()
        getters = (Mock(side_effect=RuntimeError('company unavailable')), Mock(return_value=brief['product']), Mock(return_value=brief['cta']))
        companies, service, cta = ec.settings(job, loaders=getters)
        self.assertEqual(companies, [])
        self.assertEqual(service['name'], '既婚者クラブ')
        self.assertEqual(cta['name'], '登録')
        getters[1].assert_called_once_with('s'); getters[2].assert_called_once_with('c')

    def test_bound_identity_rejects_wrong_owner_selection_and_user_instruction(self):
        job, _, _, value = fixture()
        for change in ({'tenant_id': 'other'}, {'service_id': 'other'}, {'cta_id': None}, {'custom_prompt': 'changed'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ec.validate(value, 'j', {**job, **change})
        bad = copy.deepcopy(value); bad['assignment']['product']['name'] = 'changed'
        with self.assertRaises(ValueError): ec.validate(bad, 'j')

    def test_stage_pins_before_analysis_and_reuses_without_settings_reads(self):
        job, brief, material, value = fixture()
        with patch.object(db, 'get_optional_artifact', return_value=None), patch.object(db, 'get_job', return_value=job), \
             patch.object(pb, 'prepare', return_value=(brief, material, None, 'miss')), \
             patch.object(db, 'get_learned_style_rules', return_value=[{'rule_text': '普段の言葉で具体的に説明する'}]) as rules:
            with ia.stage_scope('j', 'search_intent', '探し方'):
                self.assertEqual(ec.CURRENT.get(), value)
            rules.assert_called_once_with('owner', strict=True)
        saved = {'content_text': json.dumps(brief), 'meta': {'editorial_contract': value}}
        with patch.object(db, 'get_optional_artifact', return_value=saved), patch.object(db, 'get_job', return_value=job), \
             patch.object(db, 'get_learned_style_rules', side_effect=AssertionError('must not re-read')):
            with ia.stage_scope('j', 'article', '探し方'):
                self.assertEqual(ec.settings(job)[1]['name'], '既婚者クラブ')
                self.assertEqual(ec.CURRENT.get()['sha256'], value['sha256'])

    def test_failed_required_rule_read_cannot_start_paid_analysis(self):
        job, brief, material, _ = fixture(); paid = Mock()
        with patch.object(db, 'get_optional_artifact', return_value=None), patch.object(db, 'get_job', return_value=job), \
             patch.object(pb, 'prepare', return_value=(brief, material, None, 'miss')), \
             patch.object(db, 'get_learned_style_rules', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError), ia.stage_scope('j', 'search_intent', '探し方'): paid()
        paid.assert_not_called()

    def test_old_job_does_not_silently_change_version(self):
        with patch.object(db, 'get_optional_artifact', return_value={'content_text': '{}', 'meta': {}}), \
             patch.object(db, 'get_job', side_effect=AssertionError('legacy job need not pin')):
            with ia.stage_scope('j', 'article', '探し方'): self.assertIsNone(ec.CURRENT.get())

class EditorialSeparationTests(unittest.TestCase):
    def test_new_producers_separate_internal_notes_without_dropping_answers(self):
        from pipeline import reader_needs, reader_decisions, research_requirements as req, editorial_blueprint as bp
        from test_editorial_blueprint import fixture as blueprint_fixture
        from test_reader_value_flow import plan_fixture
        _, _, _, contract = fixture()
        with ec.use(contract):
            ns = reader_needs.output_schema(new_product=False)['format']['schema']['properties']['selected']['items']
            self.assertIn('editorial_notes', ns['required'])
            self.assertIn('editorial_notes', reader_decisions.output_schema()['items']['required'])
            self.assertIn('internal_checks', bp.schema()['properties']['sections']['items']['required'])
            self.assertIn('internal_checks', bp.repair_schema()['properties']['edits']['items']['properties']['section']['required'])
            plan = plan_fixture()
            plan['reader_decisions'][0]['editorial_notes'] = ['他社より安全という実測順位を断定しない']
            projected = ia.model_context(plan)
            self.assertEqual(projected['reader_decisions'][0]['answer_requirements'], plan['reader_decisions'][0]['answer_requirements'])
            self.assertEqual(req.plan_repair_schema(plan)['format']['schema']['properties']['reader_decisions']['items']['required'][-1], 'editorial_notes')
        _, _, _, ctx, blueprint = blueprint_fixture()
        blueprint['sections'][0]['internal_checks'] = ['どちらがより安全かという順位は作らない']
        raw = copy.deepcopy(blueprint)
        text = bp.render(blueprint, ctx)
        self.assertNotIn(blueprint['sections'][0]['internal_checks'][0], text)
        self.assertIn('閲覧と送信の適用プランを比較', text)
        self.assertIn('スタンダード', text); self.assertIn('プレミアム', text)
        self.assertEqual(blueprint, raw)
        saved = {'content_text': text, 'meta': bp.metadata(blueprint, ctx)}
        self.assertEqual(bp.require_projection(saved, ctx), blueprint)

    def test_provider_contract_is_idempotent_and_keeps_user_neutral_instruction(self):
        job, brief, material, _ = fixture()
        job['custom_prompt'] = brief['request']['custom_prompt'] = '今回は中立に比較してください'
        value = ec.build('j', job, brief, material, [])
        request = {'model': 'claude-opus-5-5', 'system': ia.STANDARD+'preserved existing prose policy',
                   'messages': [{'role': 'user', 'content': json.dumps({'reader_decisions': [
                       {'answer_requirements': ['対象プランと期間を区別する'], 'editorial_notes': ['内部注意']} ]}, ensure_ascii=False)}]}
        old = copy.deepcopy(request)
        with ec.use(value):
            once = ec.enrich_request(request)
            self.assertEqual(once, ec.enrich_request(once))
        self.assertEqual(request, old)
        self.assertNotIn(ia.STANDARD, once['system'])
        self.assertEqual(once['system'].count(ec.AUTHORITY_POLICY), 1)
        data = json.loads(once['messages'][0]['content'])
        self.assertEqual(data['editorial_contract']['assignment']['request']['custom_prompt'], '今回は中立に比較してください')
        self.assertEqual(data['reader_decisions'][0]['answer_requirements'], ['対象プランと期間を区別する'])
        self.assertIn('preserved existing prose policy', once['system'])

    def test_persisted_raw_settings_and_rules_are_used_without_database_reads(self):
        job, brief, material, _ = fixture()
        raw = {'service': {**brief['product'], 'selling_points': ['原資料の特徴']},
               'cta': {**brief['cta'], 'id': 'c', 'tenant_id': 'owner'},
               'companies': [{'name': '比較候補', 'recommend_level': -1}], 'primary_sources': []}
        value = ec.build('j', job, brief, material, [{'rule_text': '保存した文体'}], settings_snapshot=raw)
        with ec.use(value), patch.object(db.requests, 'get', side_effect=AssertionError('do not refetch')):
            self.assertEqual(db.get_service_by_id('s')['selling_points'], ['原資料の特徴'])
            self.assertEqual(db.get_cta_by_id('c')['id'], 'c')
            self.assertEqual(db.get_company_settings('owner', 'dating'), raw['companies'])
            self.assertEqual(db.get_learned_style_rules('owner'), [{'rule_text': '保存した文体'}])
            with self.assertRaises(ValueError): db.get_service_by_id('other')
            with self.assertRaises(ValueError): db.get_learned_style_rules('other')

class EditorialBoundaryTests(unittest.TestCase):
    def test_actual_claude_and_openai_adapters_receive_one_pinned_assignment(self):
        from pipeline import ai
        _, _, _, value = fixture()
        request = {'max_tokens': 1000, 'system': ia.STANDARD+'existing reader standard',
                   'messages': [{'role': 'user', 'content': '段落の意味を保って編集する'}],
                   'output_config': {'format': {'type': 'json_schema', 'schema': {'type': 'object', 'properties': {}}}}}
        calls = []
        class Captured(BaseException): pass
        def receive(**kw): calls.append(kw); raise Captured()
        client = Mock(); client.messages.stream.side_effect = receive
        with ec.use(value), patch.object(ia, 'record_request'), patch.object(ai, 'tiered_review_enabled', return_value=False), \
             patch('pipeline.openai_review.create_review_response', side_effect=receive):
            for model in ('claude-opus-5-5', 'gpt-6.1-sol'):
                with self.assertRaises(Captured): ai.create_with_retry(client, model=model, **request)
        self.assertEqual(len(calls), 2)
        for sent in calls:
            self.assertEqual(sent['system'].count(ec.AUTHORITY_POLICY), 1)
            self.assertNotIn(ia.STANDARD, sent['system'])
            self.assertIn(value['sha256'], sent['messages'][0]['content'])
            self.assertIn('既婚者クラブ', sent['messages'][0]['content'])

    def test_cache_identity_includes_the_assignment_before_reuse(self):
        from pipeline import tiered_research as tr
        from pipeline.content_quality import digest
        job, brief, material, value = fixture()
        request = {'system': 'system', 'messages': [{'role': 'user', 'content': '{}'}]}
        with ec.use(value): enriched = ec.enrich_request(request)
        text = '{"checks":[]}'
        artifact = {'content_text': text, 'meta': {'result_sha256': digest(text),
                    'request_sha256': digest(json.dumps(enriched,ensure_ascii=False,sort_keys=True))}}
        with patch.object(tr, 'get_optional_artifact', return_value=artifact):
            with ec.use(value): self.assertEqual(tr.cached_result('j','review',request), {'checks': []})
            brief['request']['custom_prompt'] = job['custom_prompt'] = '今回は中立にする'
            other = ec.build('j',job,brief,material,[])
            with ec.use(other): self.assertIsNone(tr.cached_result('j','review',request))
        self.assertEqual(request['messages'][0]['content'], '{}')

    def test_artifact_save_keeps_contract_and_original_metadata_without_network(self):
        from pipeline import article_revisions
        _, _, _, value = fixture()
        with ec.use(value), patch.object(article_revisions, 'save', side_effect=lambda j,s,data:data), \
             patch.object(db.requests, 'post', side_effect=AssertionError('not a live test')):
            saved = db.upsert_artifact('j','article','text/markdown','本文',meta={'section_map': {'entries': []}})
        self.assertEqual(saved['meta']['section_map'], {'entries': []})
        self.assertEqual(saved['meta']['editorial_contract_sha256'], value['sha256'])
        self.assertEqual(saved['content_text'], '本文')

    def test_qa_uses_same_answers_and_contract_without_making_notes_required(self):
        from pipeline import content_quality as cq, quality_context, generation_context, reader_decisions as rd
        _, _, _, value = fixture()
        decision = {'id': 'i01', 'question': 'どのプランが必要か', 'role': 'primary', 'scope': '閲覧',
                    'research_ids': ['q01'], 'depends_on': [], 'answer_requirements': ['閲覧ができる対象プラン'],
                    'editorial_notes': ['機能から安全順位を断定しない']}
        packet = {'research_plan': {'reader_decisions': [decision]}, 'reader_design': {'reader_needs': []}}
        with ec.use(value):
            req = cq.requirements_for({'article_purpose': 'cv'}, '探し方')
            self.assertEqual(req['editorial_contract'], ec.view(value))
            writer = ia.model_context(packet)
            qa = quality_context.review_requirements(packet, 'coverage')
            repair = generation_context.repair_requirements(packet)
        for consumer in (writer,qa,repair):
            self.assertEqual(consumer['research_plan']['reader_decisions'][0]['answer_requirements'], decision['answer_requirements'])
        base = {'format': {'schema': {'type':'object','properties':{},'required':[]}}}
        config = rd.review_schema(base,[decision])
        checks = config['format']['schema']['properties']['decision_reviews']['properties']['i01']['properties']
        self.assertEqual(checks['missing_requirements']['items']['type'], 'integer')
        self.assertEqual(len(decision['answer_requirements']), 1)  # notes do not add an answer index

    def test_editorial_failure_preserves_body_and_failed_review_status_with_contract(self):
        from pipeline import quality_warnings as qw
        from pipeline.content_quality import ContentQualityError
        _, _, _, value = fixture()
        article = {'content_type':'text/markdown','content_text':'既婚者クラブをおすすめします。',
                   'meta': {'editorial_contract_sha256': value['sha256']}}
        def fail(*_): raise ContentQualityError('編集上の確認が未完了')
        writes = []
        def save(**kw): writes.append(kw); return kw
        with ec.use(value), patch.object(qw, 'has_saved_draft', return_value=True), \
             patch.object(qw, 'warn') as warning, patch.object(db, 'get_artifact', return_value=article), \
             patch.object(db, 'upsert_artifact', side_effect=save):
            result = qw.scoped(fail,'content_audit')('j','探し方')
        self.assertEqual(result['content_text'], article['content_text'])
        self.assertFalse(result['meta']['publication_approved'])
        self.assertTrue(result['meta']['completion_with_warnings'])
        self.assertEqual(result['meta']['editorial_contract_sha256'], value['sha256'])
        warning.assert_called_once()

class EditorialPreflightTests(unittest.TestCase):
    def test_selected_service_cta_and_style_outages_do_not_start_the_first_paid_call(self):
        job, _, _, _ = fixture()
        service = {'id':'s','tenant_id':'owner','name':'既婚者クラブ'}
        cta = {'id':'c','tenant_id':'owner','name':'登録'}
        for failed in ('get_service_by_id','get_cta_by_id','get_company_settings','get_learned_style_rules'):
            paid = Mock()
            with patch.object(db,'get_optional_artifact',return_value=None), patch.object(db,'get_job',return_value=job), \
                 patch.object(db,'get_service_by_id',return_value=service) as read_service, \
                 patch.object(db,'get_cta_by_id',return_value=cta) as read_cta, \
                 patch.object(db,'get_primary_sources',return_value=[]), \
                 patch.object(db,'get_cached_product_context',return_value=None), \
                 patch.object(db,'get_company_settings',return_value=[]) as companies, \
                 patch.object(db,'get_learned_style_rules',return_value=[]) as rules:
                {'get_service_by_id':read_service,'get_cta_by_id':read_cta,'get_company_settings':companies,
                 'get_learned_style_rules':rules}[failed].side_effect = RuntimeError('settings outage')
                with self.subTest(failed=failed), self.assertRaises(RuntimeError):
                    with ia.stage_scope('j','search_intent','探し方'): paid()
            paid.assert_not_called()

    def test_noncommercial_and_explicit_neutral_assignments_do_not_become_forced_cv(self):
        job, brief, material, _ = fixture()
        job['article_purpose']=brief['request']['article_purpose']='awareness'
        v = ec.build('j',job,brief,material,[])
        self.assertNotIn('明確に勧め',v['assignment']['editorial_goal'])
        brief['product']=None; job['service_id']=None
        v=ec.build('j',job,brief,material,[])
        self.assertEqual(v['assignment']['product'],{})
        self.assertIn('理解・判断・行動',v['assignment']['editorial_goal'])

    def test_separated_blueprint_suffix_preserves_complete_sections(self):
        from pipeline import editorial_blueprint as bp, outline_completion as oc, tiered_research as tr
        from test_editorial_blueprint import fixture as bf
        _,_,_,context,value = bf()
        for s in value['sections']: s['internal_checks']=['内部の注意']
        _,_,_,contract = fixture()
        text=json.dumps(value,ensure_ascii=False);cut=text.index('"omissions":')
        request={'model':'claude-opus-5-5','system':'s','messages':[{'role':'user','content':'入力'}]}
        with ec.use(contract):
            request=ec.enrich_request(request)
            partial={'content_text':text[:cut], 'meta':{'stop_reason':'max_tokens','request_sha256':oc.fingerprint(request)}}
            with patch.object(tr,'get_optional_artifact',return_value=None),patch.object(oc,'get_optional_artifact',return_value=None),patch.object(tr,'checked_request',return_value=({'omissions':[]},SimpleNamespace(input_tokens=0,output_tokens=0))):
                complete,_=oc.complete('j',partial,request,context,None)
        self.assertEqual(complete,value)
        self.assertNotIn('内部の注意',bp.render(complete,context))
