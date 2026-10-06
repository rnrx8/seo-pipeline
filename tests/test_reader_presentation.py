"""Reader requirements travel through existing roles, without another paid gate."""
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import content_quality, focused_quality, step_article, step_outline, step_service_map
from pipeline.content_contract import build_content_contract
from pipeline.reader_presentation import READER_PRESENTATION_POLICY, RECOMMENDATION_REVIEW


class ReaderPresentationTests(unittest.TestCase):
    def test_citation_setting_reaches_outline_writer_and_audit_requirements(self):
        for style in ('none', 'inline_footnote', 'bottom_list', 'h2_block'):
            job = {'citation_style': style, 'must_reference_urls': 'https://required.example/source'}
            outline = step_outline._build_extra_instructions(job)
            writer = step_article._build_extra_instructions(job)
            self.assertIn('citation_style=' + style, outline)
            self.assertIn('https://required.example/source', outline)
            self.assertIn('https://required.example/source', writer)
            self.assertEqual(content_quality.requirements_for(job, '比較')['citation_style'], style)
            if style == 'none':
                self.assertIn('【出典表示】なし', writer)
                self.assertIn('本文・表に残す', writer)
            else:
                self.assertNotIn('【出典表示】なし', writer)
        self.assertIn('【出典表示】なし', step_article._build_extra_instructions({}))

    def test_writer_planner_and_placement_share_reader_policy(self):
        for module in (step_article, step_outline, step_service_map):
            self.assertIn(READER_PRESENTATION_POLICY, module.SYSTEM_PROMPT)
        for prompt in (content_quality.AUDIT_SYSTEM, content_quality.EDITORIAL_SYSTEM):
            self.assertIn(READER_PRESENTATION_POLICY, prompt)

    def test_cv_value_requirement_is_conditional_on_article_purpose(self):
        for purpose in ('cv', 'awareness'):
            contract = build_content_contract(keyword='スマホケース おすすめ',
                intent_text='Primary：スマホケースを比較して選びたい', query_attrs_text='{}',
                fact_text='', job={'article_purpose': purpose}, service={'name': 'Aケース'},
                candidate_services=['Aケース', 'Bケース'])
            featured = [s for s in contract['required_sections'] if s['key'] == 'featured_service_coverage']
            self.assertEqual(bool(featured), purpose == 'cv')
            if featured:
                self.assertEqual(featured[0]['service_name'], 'Aケース')
                self.assertTrue(any('第一候補' in s for s in featured[0]['requirements']))
                self.assertTrue(any('表' in s for s in featured[0]['requirements']))

    def test_existing_five_roles_receive_settings_and_cv_contract_without_new_call(self):
        requests = []
        contract = {'article_purpose':'cv','featured_service':'Aケース','primary_intent':'持たずに動画を見たい'}
        def send(job, step, request):
            payload = json.loads(request['messages'][0]['content'])
            requests.append((step, payload, request['system']))
            role = step.removeprefix('tiered_article_')
            keys = focused_quality.ROLES[role]
            return {'checks':[{'key':k,'status':'pass','reason':'routing fixture only','affected_blocks':[]} for k in keys]}, NS(input_tokens=0,output_tokens=0)
        with patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':''}), \
             patch('pipeline.tiered_research.checked_request',side_effect=send):
            focused_quality.audit_article(None, text='# 比較\n\nAケースの紹介。',facts='',outline='',
                contract=contract,requirements={'citation_style':'none','article_purpose':'cv'},sources='[]')
        self.assertEqual(len(requests), 5)
        for step, payload, system in requests:
            self.assertEqual(payload['requirements']['citation_style'], 'none')
            self.assertIn(READER_PRESENTATION_POLICY, system)
            if step == 'tiered_article_coverage':
                self.assertEqual(payload['content_contract'],contract)
                self.assertEqual(system.count(RECOMMENDATION_REVIEW), 1)
            else:
                self.assertNotIn(RECOMMENDATION_REVIEW, system)
            if step == 'tiered_article_organization':
                self.assertIn('個別の商品・サービス紹介',system)

    def test_reasonable_benefits_do_not_trigger_mechanical_guarantee_gate(self):
        for text in ('会員規模を重視し、相手探しの選択肢を広く持ちたい人におすすめです。',
                     '写真ぼかしで身バレのリスクを抑えながら交流できます。',
                     'スタンド付きなので、手を空けて動画を楽しめます。'):
            self.assertEqual(content_quality.explicit_risk_guarantees(text), [])
        self.assertTrue(content_quality.explicit_risk_guarantees('無料登録なのでノーリスクです。'))

    def test_deep_intent_reaches_writer_outline_and_only_coverage_review(self):
        from pipeline.reader_presentation import intent_value_context
        from pipeline.quality_context import review_requirements
        # Different industries and no ready-made catchphrase: traces must survive.
        for origin, trace in (
            ('話し相手がほしい', '交流したい→家庭と子どもの暮らしは守りたい'),
            ('転職を考えている', '環境を変えたい→収入を途切れさせたくない'),
            ('料理の手間を減らしたい', '帰宅後に休みたい→家族の食事は大切にしたい'),
        ):
            chains=[{'origin':origin,'trace':trace,'direction':'avoidance','serp_grounded':False}]
            for module in (step_article,step_outline):
                prompt=module._build_chains_prompt(chains)
                self.assertIn(origin,prompt)
                self.assertIn(trace,prompt)
            context=intent_value_context(chains)
            for role in focused_quality.ROLES:
                projected=review_requirements({'intent_value_context':context},role)
                self.assertEqual('intent_value_context' in projected,role=='coverage')

    def test_saved_intent_links_are_loaded_into_final_requirements(self):
        chains=[{'origin':'家事を楽にしたい','trace':'時間不足→家族との時間を取り戻したい',
                 'direction':'approach','concrete_phrase':'家族とゆっくり過ごしたい'}]
        with patch.object(content_quality,'requirements_for',return_value={}), \
             patch.object(content_quality,'astra_review_enabled',return_value=False), \
             patch('pipeline.db.get_optional_artifact',return_value={'content_text':json.dumps({'chains':chains})}):
            req=content_quality.final_review_requirements({'id':'other-product'},'時短家電')
        self.assertEqual(req['intent_value_context'],chains)

    def test_missing_chains_falls_back_to_analysis_only_for_coverage(self):
        from pipeline.quality_context import review_requirements
        for chains in (None, {'content_text':'invalid'}, {'content_text':'{"chains":[]}'}):
            with patch.object(content_quality,'requirements_for',return_value={}), \
                 patch.object(content_quality,'astra_review_enabled',return_value=False), \
                 patch('pipeline.db.get_optional_artifact',side_effect=lambda job,step: chains if step=='intent_chains' else {'content_text':'転職したいが収入が途切れることは避けたい'}):
                req=content_quality.final_review_requirements({'id':'job'},'転職 エージェント おすすめ')
            self.assertIn('収入',req['intent_analysis'])
            for role in focused_quality.ROLES:
                self.assertEqual('intent_analysis' in review_requirements(req,role),role=='coverage')

    def test_shared_primary_query_and_value_policy_reaches_all_authoring_stages(self):
        from pipeline import step_review
        for system in (step_outline.SYSTEM_PROMPT,step_article.SYSTEM_PROMPT,step_review.SYSTEM_PROMPT,content_quality.AUDIT_SYSTEM):
            self.assertIn('対策キーワードの対象・商材カテゴリ・問い',system)
            self.assertIn('望む状態／避けたい状況／対応する確認済みの強み／説明する場所',system)
            self.assertIn('全H2や全H3へ主KW一式を繰り返す指示ではない',system)

    def test_one_shared_definition_and_stage_tasks_do_not_redefine_order(self):
        from pipeline import step_review
        systems=(step_outline.SYSTEM_PROMPT,step_article.SYSTEM_PROMPT,step_review.SYSTEM_PROMPT,
                 content_quality.AUDIT_SYSTEM,content_quality.EDITORIAL_SYSTEM)
        for system in systems:
            self.assertEqual(system.count('【検索語・結論・推奨理由】'),1)
            self.assertEqual(system.count('【個別商材の表】'),1)
        self.assertNotIn('答え→（リスト/表）→寄り添い',step_review.SYSTEM_PROMPT)
        self.assertNotIn('③読者の不安・期待に寄り添う補完',step_article.SYSTEM_PROMPT)
        for role in ('coverage','consistency','organization'):
            self.assertIn('共通提示基準「検索語・結論・推奨理由」',focused_quality.ROLE_INSTRUCTIONS[role])
        self.assertEqual(focused_quality.ROLES['coverage'],('coverage','unfinished_content'))

    def test_comparison_intro_scope_uses_shared_policy(self):
        from pipeline import step_review
        for system in (step_outline.SYSTEM_PROMPT,step_article.SYSTEM_PROMPT,
                       step_review.SYSTEM_PROMPT,content_quality.AUDIT_SYSTEM):
            self.assertIn('短い選定理由（原則1〜2文）',system)
            self.assertIn('個々の機能、使い方、詳しい場面や葛藤の説明は当該商材H3へ',system)

    def test_outline_review_receives_intent_without_final_editorial_rules(self):
        from pipeline.quality_context import review_requirements
        with patch.object(content_quality, 'requirements_for', return_value={'keyword':'転職'}), \
             patch('pipeline.db.get_optional_artifact', side_effect=lambda job, step: None if step == 'intent_chains' else {'content_text':'職場を変えたいが収入は維持したい'}), \
             patch('pipeline.db.get_learned_style_rules', side_effect=AssertionError('final editorial rules are unrelated')):
            req = content_quality.intent_review_requirements({'id':'job'}, '転職')
        self.assertEqual(review_requirements(req, 'research')['intent_analysis'], '職場を変えたいが収入は維持したい')
        for role in ('evidence','prose','consistency','organization'):
            self.assertNotIn('intent_analysis', review_requirements(req, role))
        self.assertIn('intent_analysis', review_requirements(req, 'coverage'))
        self.assertNotIn('editorial_rules', req)

    def test_recommendation_rubric_change_invalidates_saved_audit(self):
        before = content_quality.snapshot('outline', 'facts', 'outline', {}, {})
        with patch.object(content_quality, 'RECOMMENDATION_REVIEW', 'changed acceptance rubric'):
            after = content_quality.snapshot('outline', 'facts', 'outline', {}, {})
        self.assertNotEqual(before, after)

    def test_outline_request_uses_same_acceptance_rubric_as_article_coverage(self):
        class Captured(Exception):
            pass
        sent = []
        def capture(job, step, request):
            sent.append(request)
            raise Captured()
        with patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':''}), \
             patch('pipeline.quality_context.review_reference_date', return_value='2026-10-06'), \
             patch('pipeline.tiered_research.checked_request', side_effect=capture):
            with self.assertRaises(Captured):
                content_quality.audit(None, stage='research', text='## 比較', facts='', outline='## 比較',
                    contract={}, requirements={'intent_analysis':'家庭と自分の時間を大事にしたい'}, sources='[]')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['system'].count(RECOMMENDATION_REVIEW), 1)
        payload=json.loads(sent[0]['messages'][0]['content'])
        self.assertEqual(payload['requirements']['intent_analysis'], '家庭と自分の時間を大事にしたい')
