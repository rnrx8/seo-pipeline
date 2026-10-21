import copy
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from pipeline.content_quality import ContentQualityError
from pipeline.lead_authoring import checked_focus, writer_request, writing_payload


class LeadAuthoringTest(unittest.TestCase):
    def setUp(self):
        self.design = {'reader_needs': [{'id': 'i01', 'reader_situation': '部屋を借りたが物を壁へ掛けたい',
            'motivation_context': [{'hypothesis_id': 'b1', 'motivation_links': [
                {'from': '壁へ掛けたい', 'question': 'why', 'to': '床に置くと通りづらい'},
                {'from': '穴を開けたくない', 'question': 'why', 'to': '退去時の費用が気になる'}]}]}, {'id': 'i02'}]}
        self.focus = {'need_ids': ['i01'], 'motivation_ref': None, 'felt_response': '',
                      'current_understanding': '穴を開けない方法があることは知っている',
                      'open_question': '壁を傷めず取り付けられるか', 'desired_experience': ''}

    def test_unknown_or_duplicate_audience_reference_is_rejected(self):
        for ids in ([], ['i03'], ['i01', 'i01']):
            with self.subTest(ids=ids), self.assertRaises(ContentQualityError):
                checked_focus({**self.focus, 'need_ids': ids}, self.design)

    def test_focus_requires_starting_point_but_not_an_emotional_problem(self):
        self.assertEqual(checked_focus(self.focus, self.design), self.focus)
        with self.assertRaises(ContentQualityError):
            checked_focus({**self.focus, 'current_understanding': ''}, self.design)

    def test_writer_receives_selected_focus_and_actual_article_not_raw_analysis(self):
        self.design['internal_evaluation'] = 'not authoring material'
        original = copy.deepcopy(self.focus)
        payload = writing_payload(keyword='壁 取り付け', title='壁を傷つけない取り付け方',
            reader_focus=self.focus,
            reader_design=self.design, request='短いリード', article_purpose='方法を選べる',
            article_contents=['取り付け方'], next_section_excerpt='## 取り付け方\n耐荷重を確認します。',
            facts='確認した耐荷重を守る')
        request = writer_request(payload, model='test-model')
        self.focus['open_question'] = 'changed after request'
        self.assertEqual(payload['reader_focus']['open_question'], original['open_question'])
        sent = json.loads(request['messages'][0]['content'])
        self.assertEqual(sent, payload)
        self.assertNotIn('internal_evaluation', request['messages'][0]['content'])
        self.assertEqual(sent['article_contents'], ['取り付け方'])
        self.assertEqual(request['output_config']['format']['schema']['required'], ['lead'])

    def test_selected_cause_is_projected_verbatim_without_other_branches(self):
        from pipeline.lead_authoring import focus_context
        self.focus['motivation_ref'] = {'need_id': 'i01', 'hypothesis_id': 'b1', 'link_index': 0}
        projected = focus_context(self.focus, self.design)
        self.assertEqual(projected['situation'], '床に置くと通りづらい')
        self.assertNotIn('退去時', json.dumps(projected, ensure_ascii=False))
        self.assertNotIn('motivation_ref', projected)
        for ref in ({'need_id': 'i02', 'hypothesis_id': 'b1', 'link_index': 0},
                    {'need_id': 'i01', 'hypothesis_id': 'b9', 'link_index': 0},
                    {'need_id': 'i01', 'hypothesis_id': 'b1', 'link_index': 2}):
            with self.subTest(ref=ref), self.assertRaises(ContentQualityError):
                focus_context({**self.focus, 'motivation_ref': ref}, self.design)

    def test_focus_is_shared_by_rendered_outline_and_article_review(self):
        from test_editorial_blueprint import fixture, artifact
        from pipeline import editorial_blueprint as bp, article_design
        _, _, _, ctx, value = fixture()
        ctx['reader_design']['reader_needs'][0]['motivation_context'] = self.design['reader_needs'][0]['motivation_context']
        value['reader_focus'] = {**self.focus, 'motivation_ref':
            {'need_id': 'i01', 'hypothesis_id': 'b1', 'link_index': 0}, 'felt_response': '通るたびに少し邪魔に感じる'}
        saved = artifact(value, ctx)
        self.assertIn('床に置くと通りづらい', saved['content_text'])
        self.assertIn('通るたびに少し邪魔に感じる', saved['content_text'])
        expected = article_design.design_context(saved)['reader_focus']
        self.assertEqual(expected, bp.review_context(saved)['reader_focus'])
        self.assertEqual(expected['situation'], '床に置くと通りづらい')

    def test_focus_change_invalidates_old_review_and_requires_targeted_permission(self):
        from test_editorial_blueprint import fixture, artifact, verdict
        from pipeline import editorial_blueprint as bp
        _, _, _, ctx, value = fixture()
        saved = artifact(value, ctx)
        reviewed = bp.review_context(saved)
        report = {**verdict(reviewed), 'blueprint_review_context': reviewed}
        changed_focus = {**value['reader_focus'], 'open_question': '送信できる条件を知りたい'}
        response = {'edits': [], 'delete_ids': [], 'section_order': [],
                    'lead_direction': value['lead_direction'], 'reader_focus': changed_focus}
        with self.assertRaises(ContentQualityError):
            bp.apply_edits(value, response, ctx, structural=False, targets=[])
        candidate = bp.apply_edits(value, response, ctx, structural=False, targets=[], allow_lead=True)
        self.assertEqual(candidate['sections'], value['sections'])
        self.assertNotEqual(candidate['reader_focus'], value['reader_focus'])
        with self.assertRaises(ContentQualityError):
            bp.require_review(report, artifact(candidate, ctx))

    def test_final_style_review_preserves_natural_short_reader_experience(self):
        from pipeline import step_article, step_review
        from pipeline.readability import PROSE_STYLE_POLICY
        for system in (step_article.SYSTEM_PROMPT, step_review.SYSTEM_PROMPT):
            self.assertEqual(system.count(PROSE_STYLE_POLICY), 1)
        self.assertNotIn('本文（見出し・表・リスト以外）でです・ます調でない箇所を検出', step_review.SYSTEM_PROMPT)

    def test_lead_replacement_keeps_title_body_and_spacing_exactly(self):
        from pipeline.lead_authoring import replace_lead
        prefix='# 壁へ取り付ける方法\n\n'
        body='\n\n## 取り付け方\n\n| 方法 | 制限 |\n|---|---|\n| A | B |\n\n### 確認点\n\n本文。\n'
        text=prefix+'元のリード。'+body
        self.assertEqual(replace_lead(text,'新しいリード。'),prefix+'新しいリード。'+body)
        for bad in ('', '## 新しい章\n本文', '```html\n本文\n```'):
            with self.subTest(bad=bad),self.assertRaises(ContentQualityError):replace_lead(text,bad)
        for bad_text in ('タイトルなし\n\n## 章\n本文', '# タイトル\n\n本文だけ', '# タイトル\n\n## 章\n本文'):
            with self.subTest(text=bad_text),self.assertRaises(ContentQualityError):replace_lead(bad_text,'新稿。')

    def test_final_lead_call_is_bounded_source_linked_and_not_a_semantic_pass(self):
        from test_editorial_blueprint import fixture, artifact
        from pipeline import step_article
        _,_,_,ctx,value=fixture();saved=artifact(value,ctx)
        text='# 利用条件\n\n元のリード。\n\n## 閲覧と送信\n\n本文。\n\n## まとめ\n\n既存のまとめ。'
        message=NS(stop_reason='end_turn',content=[NS(type='text',text='{"lead":"用途に合う条件を確かめます。"}')],usage=NS(input_tokens=3,output_tokens=2))
        with patch.object(step_article,'create_with_retry',return_value=message) as send:
            result,ti,to,meta=step_article._finish_reader_lead(None,text,saved,'利用条件',
                {'custom_prompt':'サービス名は次章へ','tone_style':'丁寧'},[{'rule_text':'自然な短文'}])
        self.assertEqual(send.call_count,1);self.assertEqual((ti,to),(3,2))
        sent=json.loads(send.call_args.kwargs['messages'][0]['content'])
        self.assertEqual(sent['draft_lead'],'元のリード。')
        self.assertEqual(sent['requirements']['learned_style_rules'],['自然な短文'])
        self.assertNotIn('既存のまとめ',sent['next_section_excerpt'])
        self.assertEqual(sent['article_contents'],['閲覧と送信','まとめ'])
        self.assertTrue(meta['body_preserved']);self.assertFalse(meta['semantically_accepted'])
        self.assertIn('既存のまとめ',result)
        message.stop_reason='max_tokens'
        with patch.object(step_article,'create_with_retry',return_value=message),self.assertRaises(ContentQualityError):
            step_article._finish_reader_lead(None,text,saved,'利用条件',{},[])

    def test_lead_limit_honors_explicit_lead_lengths_but_not_body_counts(self):
        from pipeline.lead_authoring import lead_char_limit
        for prompt,expected in [('本文5000字、リードは短く',220),('リードは短く、本文5000字',220),
            ('200字前後のリード',220),('リードは100字以内',100),('リード文は300〜400字',400),('500文字程度のリード',550)]:
            with self.subTest(prompt=prompt):self.assertEqual(lead_char_limit({'custom_prompt':prompt}),expected)

    def test_length_correction_is_bounded_and_keeps_original_body(self):
        from test_editorial_blueprint import fixture, artifact
        from pipeline import step_article
        _,_,_,ctx,value=fixture();saved=artifact(value,ctx)
        text='# 条件\n\n元のリード。\n\n## 閲覧と送信\n\n本文。'
        def message(lead):return NS(stop_reason='end_turn',content=[NS(type='text',text=json.dumps({'lead':lead}))],usage=NS(input_tokens=3,output_tokens=2))
        with patch.object(step_article,'create_with_retry',side_effect=[message('長'*221),message('短いリード。')]) as send:
            result,ti,to,meta=step_article._finish_reader_lead(None,text,saved,'条件',{},[])
        self.assertEqual(send.call_count,2);self.assertEqual((ti,to),(6,4))
        self.assertEqual(meta['status'],'edited_pending_qa');self.assertTrue(meta['body_preserved'])
        self.assertEqual(meta['draft_lead'],'元のリード。');self.assertEqual(len(meta['attempts']),2)
        self.assertIn('実測221字',json.loads(send.call_args.kwargs['messages'][0]['content'])['scope_feedback'])
        with patch.object(step_article,'create_with_retry',side_effect=[message('長'*221),message('長'*222)]) as send:
            _,_,_,meta=step_article._finish_reader_lead(None,text,saved,'条件',{},[])
        self.assertEqual(send.call_count,2);self.assertEqual(meta['status'],'needs_length_revision')
        self.assertFalse(meta['semantically_accepted'])


if __name__ == '__main__':
    unittest.main()
