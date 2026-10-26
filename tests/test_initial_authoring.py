import copy
import json
import unittest
from unittest.mock import patch
from pipeline.initial_authoring import assignment, brief_from_settings, model_context, model_evidence, record_request
from pipeline.quality_budget import scope


class InitialAuthoringTests(unittest.TestCase):
    def test_generated_rules_do_not_override_request_or_drop_factual_conditions(self):
        raw = {'reader_design': {'reader_needs': [{
            'id': 'i1', 'question': 'どちらを選ぶか', 'tone': '中立にする',
            'answer_order': '両者を均等に勧める', 'answer_requirements': ['免責を書く'],
            'reader_situation': '話しても反応が薄い', 'background_emotion': 'さみしい',
            'desired_state': '日々の出来事を話し合いたい',
            'motivation_context': [{'motivation_links': [{'from': '話したい', 'to': '反応をもらう時間がほしい'}]}]}]},
            'reader_decisions': [{'id': 'i1', 'question': 'どちらを選ぶか', 'role': 'primary',
                                  'depends_on': [], 'research_ids': ['q1'], 'answer_requirements': ['均等推薦']}],
            'accepted_claims': {'q1.c1': {'text': '機能Aを使える', 'conditions': [{'dimension': 'plan', 'value': '有料上位プランのみ'}],
                'source_ids': ['ref1']}}, 'sources': {'ref1': {'url': 'https://official.example/plan'}},
            'publication_conditions': ['男性のみ'], 'claim_limits': ['安全の順位は比較不可']}
        before = copy.deepcopy(raw)
        sent = model_context(raw)
        for key in ('tone', 'answer_order', 'answer_requirements'):
            self.assertNotIn(key, sent['reader_design']['reader_needs'][0])
        self.assertNotIn('answer_requirements', sent['reader_decisions'][0])
        for key in ('accepted_claims', 'sources', 'publication_conditions', 'claim_limits'):
            self.assertEqual(sent[key], raw[key])
        for key in ('reader_situation', 'background_emotion', 'desired_state', 'motivation_context'):
            self.assertEqual(sent['reader_design']['reader_needs'][0][key], raw['reader_design']['reader_needs'][0][key])
        self.assertEqual(raw, before)
        brief = brief_from_settings({'article_purpose': 'cv', 'tone_style': '親しみやすく',
            'custom_prompt': 'この比較は中立に書く'}, {'name': 'A', 'must_exclude': '成果保証',
            'selling_points': ['未検証の効果99%']})
        task = assignment(brief)
        self.assertEqual(task['request']['custom_prompt'], 'この比較は中立に書く')
        self.assertEqual(task['request']['tone_style'], '親しみやすく')
        self.assertEqual(task['product']['must_exclude'], '成果保証')
        self.assertNotIn('99%', json.dumps(task, ensure_ascii=False))

    def test_no_cv_target_is_invented_for_other_purpose_or_missing_product(self):
        for brief in ({'request': {'article_purpose': 'awareness'}, 'product': {'name': 'A'}},
                      {'request': {'article_purpose': 'cv'}, 'product': None}):
            self.assertNotIn('を明確に勧め', assignment(brief)['editorial_goal'])

    def test_projection_keeps_legacy_text_and_evidence_policy(self):
        self.assertEqual(model_evidence('旧形式の資料。'), '旧形式の資料。')
        self.assertEqual(model_evidence('根拠の方針\n{"sources": {"ref1": "原文"}}'),
                         '根拠の方針\n{"sources": {"ref1": "原文"}}')

    def test_exact_provider_request_is_saved_and_snapshot_failure_is_advisory(self):
        request = {'model': 'native', 'system': '編集基準', 'max_tokens': 1200,
                   'messages': [{'role': 'user', 'content': '制作依頼と資料'}]}
        with scope('job', 'article'), patch('pipeline.db.upsert_artifact') as save:
            record_request(request)
        args, kwargs = save.call_args
        self.assertEqual(args[:3], ('job', args[1], 'application/json'))
        self.assertEqual(json.loads(args[3])['request'], request)
        self.assertEqual(json.loads(args[3])['request_state'], 'prepared_not_send_confirmation')
        self.assertIn('request_sha256', kwargs['meta'])
        with scope('job', 'article'), patch('pipeline.db.upsert_artifact', side_effect=RuntimeError('unavailable')):
            record_request(request)
        with patch('pipeline.db.upsert_artifact') as save:
            record_request(request)
            save.assert_not_called()

    def test_sdk_tool_conversation_snapshot_matches_plain_provider_input(self):
        from anthropic.types import TextBlock, ToolUseBlock
        request = {'model':'native', 'messages':[
            {'role':'assistant', 'content':[
                TextBlock(type='text', text='取得します。'),
                ToolUseBlock(type='tool_use', id='tool1', name='fetch', input={'url':'https://example.com'})]},
            {'role':'user', 'content':[{'type':'tool_result','tool_use_id':'tool1','content':'原文'}]}]}
        original = copy.deepcopy(request)
        plain = json.loads(json.dumps(request, default=lambda b:b.model_dump(mode='json')))
        with scope('job','article'), patch('pipeline.db.upsert_artifact') as save:
            record_request(request)
            record_request(plain)
        self.assertEqual(save.call_count,2)
        first, second = [json.loads(c.args[3]) for c in save.call_args_list]
        self.assertEqual(first['request'],plain)
        self.assertEqual(first['request_sha256'],second['request_sha256'])
        self.assertEqual(request,original)

    def test_declared_child_reference_maps_to_its_owner_without_editing_answers(self):
        from pipeline.editorial_blueprint import canonicalize_research_refs, validate
        from test_reader_progression import chain
        _, _, _, ctx, value = chain()
        ctx['reader_design']['reader_needs'][0]['hypothesis_ids'] = ['b1']
        value['sections'][0]['need_ids'].extend(['b1', 'i01'])
        before = copy.deepcopy(value)
        fixed = canonicalize_research_refs(value, ctx)
        self.assertEqual(fixed['sections'][0]['need_ids'], ['i01'])
        self.assertEqual(fixed['sections'][0]['answer_elements'], before['sections'][0]['answer_elements'])
        self.assertEqual(fixed['sections'][0]['reader_progression'], before['sections'][0]['reader_progression'])
        validate(fixed, ctx)
        self.assertEqual(value, before)
        value['sections'][0]['need_ids'] = []
        inherited = canonicalize_research_refs(value, ctx)
        self.assertEqual(inherited['sections'][0]['need_ids'], ['i01'])
        validate(inherited, ctx)
        value['sections'][0]['need_ids'] = ['unknown']
        from pipeline.content_quality import ContentQualityError
        with self.assertRaises(ContentQualityError):
            validate(canonicalize_research_refs(value, ctx), ctx)

    def test_repair_receives_same_goal_without_replacing_shared_quality_or_calling_model(self):
        from pipeline import step_content_audit
        from pipeline.reader_presentation import READER_PRESENTATION_POLICY
        from pipeline.initial_authoring import STANDARD
        packet = {'requirements': {}, 'article_blocks': []}
        with patch.object(step_content_audit, 'require_article_repair_scope'), \
             patch('pipeline.quality_context.compact_enabled', return_value=True), \
             patch('pipeline.review_scope.repair_packet', return_value=packet):
            request, _ = step_content_audit.prepare_content_repair(None, job_id='job', text='本文。',
                report={'checks': [], 'editorial_findings': []},
                requirements={'article_purpose': 'cv', 'tone_style': '読者に直接語る', 'custom_prompt': '明確に勧める'},
                sources='[]', facts='', contract={'featured_service': 'A'}, issues=[])
        payload = json.loads(request['messages'][0]['content'])
        self.assertIn('Aを明確に勧め', payload['editorial_assignment']['editorial_goal'])
        self.assertEqual(payload['editorial_assignment']['request']['tone_style'], '読者に直接語る')
        self.assertEqual(request['system'].count(READER_PRESENTATION_POLICY), 1)
        self.assertEqual(request['system'].count(STANDARD), 1)

    def test_registered_product_rules_survive_final_qa_and_repair(self):
        from pipeline import content_quality, step_content_audit
        source=assignment(brief_from_settings({'article_purpose':'cv','tone_style':'普段の言葉'},
            {'name':'A','must_include':'条件と料金を対応させる','must_exclude':'禁止表現'}))
        article={'content_text':'本文。','meta':{'editorial_assignment':copy.deepcopy(source)}}
        outline={'content_text':'構成。','meta':{'blueprint_version':'fixture'}}
        with patch.object(content_quality,'intent_review_requirements',return_value={}), \
             patch.object(content_quality,'astra_review_enabled',return_value=False), \
             patch('pipeline.db.get_optional_artifact',side_effect=lambda j,s:article if s=='article' else outline), \
             patch('pipeline.article_design.design_context',return_value={}):
            requirements=content_quality.final_review_requirements({'id':'job'},'比較')
        self.assertEqual(requirements['editorial_assignment'],source)
        packet={'requirements':{},'article_blocks':[]}
        with patch.object(step_content_audit,'require_article_repair_scope'), \
             patch('pipeline.quality_context.compact_enabled',return_value=True), \
             patch('pipeline.review_scope.repair_packet',return_value=packet):
            request,_=step_content_audit.prepare_content_repair(None,job_id='job',text='本文。',
                report={'checks':[],'editorial_findings':[]},requirements=requirements,
                sources='[]',facts='',contract={'featured_service':'A'},issues=[])
        sent=json.loads(request['messages'][0]['content'])['editorial_assignment']
        self.assertEqual(sent,source)
        self.assertEqual(sent['product']['must_exclude'],'禁止表現')
        requirements['editorial_assignment']['product']['must_exclude']='変更'
        self.assertEqual(article['meta']['editorial_assignment']['product']['must_exclude'],'禁止表現')
