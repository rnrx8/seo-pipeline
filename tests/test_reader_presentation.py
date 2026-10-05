"""Reader requirements travel through existing roles, without another paid gate."""
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline import content_quality, focused_quality, step_article, step_outline, step_service_map
from pipeline.content_contract import build_content_contract
from pipeline.reader_presentation import READER_PRESENTATION_POLICY


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
            if step == 'tiered_article_organization':
                self.assertIn('個別の商品・サービス紹介',system)

    def test_reasonable_benefits_do_not_trigger_mechanical_guarantee_gate(self):
        for text in ('会員規模を重視し、相手探しの選択肢を広く持ちたい人におすすめです。',
                     '写真ぼかしで身バレのリスクを抑えながら交流できます。',
                     'スタンド付きなので、手を空けて動画を楽しめます。'):
            self.assertEqual(content_quality.explicit_risk_guarantees(text), [])
        self.assertTrue(content_quality.explicit_risk_guarantees('無料登録なのでノーリスクです。'))
