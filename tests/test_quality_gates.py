import copy
import json
import unittest
from unittest.mock import patch
from pipeline.readability import readability_issues
from pipeline.section_identity import bind_sections,carry_sections,valid_binding
from pipeline.research_requirements import validate_matrix
from pipeline.focused_quality import ROLES
from pipeline import content_quality as q
from quality_fixtures import responses

class QualityGateTests(unittest.TestCase):
    def test_blank_lines_bold_and_html_do_not_reset_prose(self):
        for separator in ['\n\n','\n\n**強調**\n\n','\n<div></div>\n']:
            findings=readability_issues('## 説明\n'+'あ'*170+separator+'い'*170)
            self.assertEqual(len(findings),1)
            self.assertGreater(findings[0]['characters'],300)
            self.assertTrue(findings[0]['affected_blocks'])

    def test_real_lists_tables_and_subheadings_split_prose(self):
        for separator in ['\n- 要点\n','\n| 項目 | 値 |\n|---|---|\n| a | b |\n','\n#### 別の話題\n']:
            self.assertFalse(readability_issues('## 説明\n'+'あ'*170+separator+'い'*170))
        self.assertFalse(readability_issues('## 説明\n'+'あ'*300))
        self.assertTrue(readability_issues('#### 小見出し\n'+'あ'*301))

    def test_h4_addition_keeps_existing_section_ids(self):
        outline='### H2：比較\n#### H3：料金\n##### H4：条件\n### H2：まとめ'
        before='## 比較\n説明\n### 料金\n料金の説明\n#### 条件\n既存条件\n## まとめ\nまとめ'
        after=before.replace('料金の説明','料金の説明\n#### 支払方法\n新しい区切り')
        binding=bind_sections(before,outline)
        updated=carry_sections(before,after,outline,binding,allow_h4=True)
        self.assertTrue(valid_binding(after,outline,updated))
        self.assertEqual([e['id'] for e in updated['entries'] if not e['id'].startswith('layout-')],[e['id'] for e in binding['entries']])
        for invalid in [after.replace('#### 条件\n',''), before.replace('## 比較\n','## 比較\n#### 不正\n')]:
            with self.assertRaises(ValueError):carry_sections(before,invalid,outline,binding,allow_h4=True)

    def test_missing_required_question_and_fabricated_quote_fail(self):
        plan={'items':[{'id':'q1','question':'解約条件','required':True}]}
        pages=[{'url':'https://official.test/terms','text':'返金はできません。'}]
        value={'items':[{'id':'q1','status':'confirmed','answer':'返金不可','reason':'確認','evidence':[{'url':pages[0]['url'],'quote':'返金はできません。'}]}]}
        self.assertFalse(validate_matrix(copy.deepcopy(value),plan,pages))
        bad=copy.deepcopy(value);bad['items'][0]['evidence'][0]['quote']='返金できます。'
        self.assertTrue(validate_matrix(bad,plan,pages))
        for status in ['unresearched','fetch_failed','searched_not_found']:
            bad=copy.deepcopy(value);bad['items'][0]['status']=status
            self.assertTrue(validate_matrix(bad,plan,pages))
        with self.assertRaises(q.ContentQualityError):validate_matrix({'items':[]},plan,pages)

    def test_five_roles_share_snapshot_and_all_are_required(self):
        saved=[]
        with patch.object(q,'create_with_retry',side_effect=responses()) as calls:
            result=q.audit(None,stage='article',text='# 記事\n\n## 説明\n\n本文です。',facts='',outline='',contract={},requirements={},checkpoint=lambda role,p:saved.append(p))
        self.assertEqual(calls.call_count,5)
        self.assertEqual(len(saved),5)
        self.assertEqual({p['snapshot'] for p in saved},{result['snapshot']})
        q.require_audit(result,result['snapshot'],stage='article')
        for role in ROLES:
            bad=copy.deepcopy(result);del bad['phases'][role]
            with self.assertRaises(q.ContentQualityError):q.require_audit(bad,result['snapshot'],stage='article')
        bad=copy.deepcopy(result);bad['phases']['language']['snapshot']='stale'
        with self.assertRaises(q.ContentQualityError):q.require_audit(bad,result['snapshot'],stage='article')

    def test_prior_phase_persisted_when_later_response_fails(self):
        saved=[]
        with patch.object(q,'create_with_retry',side_effect=[responses()[0],q.ContentQualityError('interrupted')]):
            with self.assertRaises(q.ContentQualityError):
                q.audit(None,stage='article',text='# 記事',facts='',outline='',contract={},requirements={},checkpoint=lambda role,p:saved.append(role))
        self.assertEqual(saved,['evidence'])
