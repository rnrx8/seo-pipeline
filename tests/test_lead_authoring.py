import copy
import json
import unittest

from pipeline.content_quality import ContentQualityError
from pipeline.lead_authoring import checked_focus, writer_request, writing_payload


class LeadAuthoringTest(unittest.TestCase):
    def setUp(self):
        self.design = {'reader_needs': [{'id': 'i01', 'reader_situation': '部屋を借りたが物を壁へ掛けたい',
            'motivation_context': [{'hypothesis_id': 'b1', 'motivation_links': [
                {'from': '壁へ掛けたい', 'question': 'why', 'to': '床に置くと通りづらい'},
                {'from': '穴を開けたくない', 'question': 'why', 'to': '退去時の費用が気になる'}]}]}, {'id': 'i02'}]}
        self.focus = {'need_ids': ['i01'], 'motivation_ref': None,
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
            reader_focus=self.focus, lead_direction='取り付けたい物から方法の違いへ進む',
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


if __name__ == '__main__':
    unittest.main()
