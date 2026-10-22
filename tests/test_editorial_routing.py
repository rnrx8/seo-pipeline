import copy
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import focused_quality
from pipeline.quality_context import EDITORIAL_OWNERS, editorial_rules_for_role, review_requirements
from pipeline.readability import long_sentence_candidates
from pipeline.review_scope import repair_packet
from pipeline.step_review import SYSTEM_PROMPT


class EditorialRoutingTests(unittest.TestCase):
    def setUp(self):
        self.rules = SYSTEM_PROMPT.split('【チェック・修正項目】', 1)[1].split('【出力フォーマット】', 1)[0]

    def test_builtin_sections_are_preserved_verbatim_in_assigned_roles(self):
        sections = re.split(r'(?m)(?=^### 【|^\d+\. )', self.rules)
        for prefix, owners in EDITORIAL_OWNERS.items():
            section = next(s for s in sections if s.startswith(prefix))
            for role in focused_quality.ROLES:
                projected = editorial_rules_for_role(self.rules, role)
                self.assertEqual(section in projected, role in owners, (prefix, role))

    def test_unknown_and_user_rules_are_never_silently_dropped(self):
        for rules in ('独自ルール。一文は100字まで。', self.rules + '\n12. 新しい基準\n必須の内容。'):
            for role in focused_quality.ROLES:
                self.assertEqual(editorial_rules_for_role(rules, role), rules)
        original = {'editorial_rules': self.rules, 'learned_style_rules': ['語尾の指定'], 'custom_prompt': '独自指定'}
        before = copy.deepcopy(original)
        for role in focused_quality.ROLES:
            result = review_requirements(original, role)
            self.assertEqual(result['learned_style_rules'], original['learned_style_rules'])
            self.assertEqual(result['custom_prompt'], original['custom_prompt'])
            # Shared projection is also used by repair: it must retain all rules.
            self.assertEqual(result['editorial_rules'], self.rules)
        self.assertEqual(original, before)

    def test_language_hints_do_not_add_requests_or_automatic_failure(self):
        text = '# 記事\n\n## 使い方\n\n' + 'あ' * 80 + '。'
        requests = []
        def send(job, step, request):
            payload = json.loads(request['messages'][0]['content'])
            role = step.removeprefix('tiered_article_')
            requests.append((role, payload))
            return {'checks': [{'key': key, 'status': 'pass', 'reason': '文脈による許容', 'affected_blocks': []}
                               for key in focused_quality.ROLES[role]]}, SimpleNamespace(input_tokens=0, output_tokens=0)
        with patch('pipeline.ai.tiered_review_enabled', return_value=True), \
             patch('pipeline.quality_context.compact_enabled', return_value=False), \
             patch('pipeline.tiered_research.checked_request', side_effect=send):
            report = focused_quality.audit_article(None, text=text, facts='', outline='', contract={},
                requirements={'editorial_rules': self.rules}, sources='')
        self.assertEqual(len(requests), 5)
        self.assertTrue(report['valid'])
        for role, payload in requests:
            self.assertEqual('long_sentence_candidates' in payload, role == 'language')
            if role == 'language':
                self.assertEqual(payload['long_sentence_candidates'][0]['characters'], 81)
            if role == 'organization':
                self.assertNotIn('6. 長文チェック', payload['requirements']['editorial_rules'])

    def test_language_findings_reach_existing_scoped_repair_without_extra_audit(self):
        text = '# 記事\n\n## 選び方\n\n' + 'あ' * 80 + '。\n\n別の内容です。'
        candidate = long_sentence_candidates(text)[0]
        report = {'checks': [{'key': 'prose_quality', 'status': 'fail', 'reason': '一文の整理',
                              'affected_blocks': [{'id': candidate['id'], 'reason': '一文の整理'}]}]}
        packet = repair_packet(text, {}, [], report)
        self.assertEqual(packet['target_block_ids'], [candidate['id']])
        self.assertFalse(packet['source_documents'])

    def test_lead_location_reaches_only_language_without_title_or_body(self):
        text = '# 記事\n\n導入の一段落。\n\n導入の続き。\n\n## 使い方\n\n本文です。'
        requests = []
        def send(job, step, request):
            role = step.removeprefix('tiered_article_')
            requests.append((role, json.loads(request['messages'][0]['content'])))
            return {'checks': [{'key': key, 'status': 'pass', 'reason': '文脈による許容', 'affected_blocks': []}
                               for key in focused_quality.ROLES[role]]}, SimpleNamespace(input_tokens=0, output_tokens=0)
        with patch('pipeline.ai.tiered_review_enabled', return_value=True), \
             patch('pipeline.quality_context.compact_enabled', return_value=False), \
             patch('pipeline.tiered_research.checked_request', side_effect=send):
            report = focused_quality.audit_article(None, text=text, facts='', outline='', contract={},
                requirements={'editorial_rules': self.rules}, sources='')
        self.assertEqual(len(requests), 5)
        self.assertTrue(report['valid'])
        for role, payload in requests:
            self.assertEqual('lead_block_ids' in payload, role == 'language')
            if role == 'language':
                selected = [b['text'] for b in payload['article_blocks'] if b['id'] in payload['lead_block_ids']]
                self.assertEqual(selected, ['導入の一段落。', '導入の続き。'])


class SentenceCandidateTests(unittest.TestCase):
    def test_threshold_visible_characters_and_separate_sentences(self):
        self.assertEqual(long_sentence_candidates('あ' * 79 + '。'), [])
        self.assertEqual(long_sentence_candidates('あ' * 79 + '。' + 'い' * 79 + '。'), [])
        value = long_sentence_candidates('**' + 'あ' * 40 + '**[リンク](https://example.com/' + 'x' * 150 + ')\n' + 'い' * 40 + '。')
        self.assertEqual(value[0]['characters'], 84)

    def test_nonprose_is_not_miscounted(self):
        long = 'あ' * 100
        for text in ('## ' + long, '- ' + long, '1. ' + long, '```\n' + long + '\n```',
                     '| 項目 | 値 |\n|---|---|\n|説明|' + long + '|',
                     '項目 | 値\n--- | ---\n説明 | ' + long,
                     '<!--' + long + '-->短文。'):
            self.assertEqual(long_sentence_candidates(text), [], text)

    def test_existing_missed_sentence_is_identified_without_product_specific_logic(self):
        sentence = '浅野総合法律事務所の浅野英之弁護士は、アプリの利用自体は違法ではないとしたうえで、不貞行為に発展すれば配偶者から慰謝料を請求されたり、離婚の争いで不利に扱われたりするおそれがあると解説しています。'
        result = long_sentence_candidates('# 記事\n\n' + sentence)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['sentence'], sentence)
        self.assertEqual(result[0]['characters'], len(sentence))
        self.assertEqual(long_sentence_candidates(sentence.replace('としたうえで、', 'としています。')), [])
