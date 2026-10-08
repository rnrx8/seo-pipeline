import json
import unittest
from unittest.mock import patch
from pipeline.outline_design import assignment_issues, topic_assignments
from pipeline.outline_length import planning_setting
from pipeline import step_outline, step_article
from pipeline.content_contract import build_content_contract


PLAN = {'items': [
    {'id': 'q01', 'question': '主な危険は何か', 'priority': 'essential', 'required': True},
    {'id': 'q02', 'question': '補助的な統計', 'priority': 'supporting', 'required': False},
]}
BRIEF = '''### H2：主な危険とは？
- 読者の疑問：どのような被害があるか
- 対応論点：q01
- 回答要素：金銭被害と個人情報の流出を区別し、次の詳細章へつなぐ
'''


class OutlineDesignTests(unittest.TestCase):
    def test_purpose_changes_service_role_without_losing_information_intent(self):
        for purpose, treatment in (("cv", "integrated"), ("awareness", "none"), ("", "none")):
            job = {"article_purpose": purpose}
            contract = build_content_contract(keyword="既婚者マッチングアプリ やばい",
                intent_text="Primary：リスクを理解したい", query_attrs_text="{}", fact_text="",
                job=job, service={"name":"既婚者クラブ"})
            self.assertEqual(contract["service_treatment"], treatment)
            self.assertEqual(contract["primary_intent"], "Primary：リスクを理解したい")
            prompt = step_outline._build_extra_instructions(job)
            if purpose:
                self.assertIn("【記事目的】" + purpose, prompt)
                self.assertIn("論点の採否・章の役割", prompt)
            else:
                self.assertNotIn("【記事目的】", prompt)

    def test_missing_required_topic_fails_but_unused_supporting_does_not(self):
        self.assertEqual(assignment_issues(BRIEF, PLAN), [])
        self.assertEqual(topic_assignments(BRIEF)[0]['ids'], ['q01'])
        issues = assignment_issues(BRIEF.replace('q01', 'q02'), PLAN)
        self.assertIn('unassigned_required_topic', [i['key'] for i in issues])

    def test_unknown_ids_and_empty_answer_design_fail(self):
        self.assertIn('unknown_outline_topic', [i['key'] for i in assignment_issues(BRIEF.replace('q01', 'q999'), PLAN)])
        self.assertIn('missing_outline_answer_design', [i['key'] for i in assignment_issues(BRIEF.split('- 回答要素')[0], PLAN)])

    def test_bold_labels_and_nested_values_keep_same_design(self):
        text = '''### H2：主な危険とは？
- **読者の疑問**：どのような被害があるか
- **対応論点**：
  - q01
- **回答要素**
  - 金銭被害
  - 個人情報の流出
- **表現形式**：比較表
'''
        self.assertEqual(assignment_issues(text, PLAN), [])
        self.assertEqual(topic_assignments(text)[0]['ids'], ['q01'])

    def test_empty_nested_answer_does_not_borrow_other_fields(self):
        for empty in ('', '\n  -', '\n  - ** **'):
            text = BRIEF.split('- 回答要素')[0] + '- **回答要素**：' + empty + '\n- 掲載項目：金銭被害\n'
            self.assertIn('missing_outline_answer_design', [i['key'] for i in assignment_issues(text, PLAN)])

    def test_question_ids_in_other_fields_are_not_assignments(self):
        text = BRIEF.replace('- 対応論点：q01', '- 対応論点：\n- 回答要素：q01を説明')
        self.assertIn('unassigned_required_topic', [i['key'] for i in assignment_issues(text, PLAN)])

    def test_qa_and_video_do_not_change_article_length_reference(self):
        pages = [{'url':'https://example.com/article', 'fetch_status':'success', 'word_count':4000}]
        reference = step_outline._calc_target_word_count(json.dumps({'competitor_headings': pages}))
        for url in ('https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/1', 'https://www.reddit.com/r/test', 'https://www.youtube.com/watch?v=123'):
            pages.append({'url':url, 'fetch_status':'success', 'word_count':29000})
        self.assertEqual(step_outline._calc_target_word_count(json.dumps({'competitor_headings': pages})), reference)

    def test_reference_does_not_expand_complete_content_based_allocations(self):
        text = BRIEF + '''\n## セクション別ボリューム設計
| 主な危険とは？ | 5 | 600字 | 二つの危険を概要として説明する |
'''
        self.assertEqual(step_outline.ensure_complete_volume_design(text, '14,000字', reference_only=True), (text, False))
        self.assertEqual(planning_setting({}, {'meta':{'length_reference':'14,000字'}}), '14,000字')
        self.assertEqual(planning_setting({'word_count_setting':'5,000字'}, {'meta':{'length_reference':'14,000字'}}), '5,000字')

    def test_complete_short_part_does_not_buy_rewrite_for_estimated_length(self):
        # The required answer is present; the automatic 1600-char allocation is not a missing-answer finding.
        text = '## 主な危険とは？\n金銭被害と個人情報の流出です。'
        with patch.object(step_article, '_call', return_value=(text, 10, 20)) as call:
            result = step_article._write_complete_part(None, [{'role':'user','content':'執筆'}],
                2000, BRIEF, [('主な危険とは？',5,1600)], 1)
        self.assertEqual(result[0], text)
        self.assertEqual(call.call_count, 1)
