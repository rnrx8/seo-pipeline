import copy
import hashlib
import unittest
from unittest.mock import patch
from pipeline import registered_materials as materials, quality_context, reader_qa
from pipeline.editorial_contract import CURRENT
from pipeline.reader_presentation import PUBLICATION_DATE_POLICY
from pipeline.step_fact_sheet import _build_primary_sources_prompt

class RegisteredMaterialHandoffTests(unittest.TestCase):
    def test_full_pinned_guide_and_only_dependent_question_receive_original(self):
        text = '目次\n' + '前半' * 1100 + '\n後半の個別調査URL https://official.example/study/25'
        row = {'id': 'source:123', 'title': '登録調査 選択ガイド', 'origin': 'registered_material', 'text': text}
        original = {'product_input': {'materials': [row]}}
        token = CURRENT.set(copy.deepcopy(original))
        try:
            question = {'id': 'q13', 'question': '「登録調査 選択ガイド」から該当調査を選ぶ'}
            selected = materials.attach({'plan': {'items': [question]}}, questions=[question])
            document = selected['registered_materials'][0]
            self.assertEqual(document['text'], text)
            self.assertIn('https://official.example/study/25', document['text'])
            self.assertEqual(document['text_sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(document['verification'], 'supplied_not_verified')
            self.assertFalse(document['truncated'])
            unrelated = {'plan': {'items': [{'id': 'q01', 'question': '料金は？'}]}}
            self.assertEqual(materials.attach(copy.deepcopy(unrelated), questions=unrelated['plan']['items']), unrelated)
            self.assertEqual(CURRENT.get(), original)
        finally:
            CURRENT.reset(token)

    def test_explicit_document_ids_and_absence_do_not_invent_sources(self):
        data = {'materials': [{'id': 'a', 'title': '料金', 'origin': 'registered_material', 'text': '未検証資料'},
                              {'id': 'b', 'origin': 'registered_service', 'text': '商材説明'}]}
        self.assertEqual(materials.documents(data, questions=[{'material_ids': ['a']}])[0]['id'], 'a')
        self.assertEqual(materials.documents(data, questions=[{'question': '価格は？'}]), [])
        token = CURRENT.set(None)
        try:
            self.assertEqual(materials.attach({'original': True}), {'original': True})
        finally:
            CURRENT.reset(token)

    def test_unchanged_qa_roles_keep_their_original_paid_request_context(self):
        requirements = {'custom_prompt': '資料を活用する', 'article_design': {'sections': []}}
        with_material = {**requirements, 'registered_materials': [{'text': '登録原文'}]}
        for role in ('evidence', 'language', 'consistency', 'organization'):
            self.assertEqual(quality_context.review_requirements(requirements, role),
                             quality_context.review_requirements(with_material, role))
        self.assertIn('registered_materials', quality_context.review_requirements(with_material, 'coverage'))

    def test_registered_excerpt_and_chapter_qa_use_same_source_and_date_authority(self):
        text = 'x' * 3532
        self.assertIn(text, _build_primary_sources_prompt([{'title': '資料', 'content_text': text}]))
        self.assertIn(PUBLICATION_DATE_POLICY, reader_qa.CONTEXT_POLICY)
        self.assertIn('本文中の出来事・料金・調査結果などに必要な実際の対象日付は保持', PUBLICATION_DATE_POLICY)
