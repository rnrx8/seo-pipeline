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
        for invalid in [before.replace('#### 条件\n',''), before.replace('## 比較\n','## 比較\n#### 不正\n')]:
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

    def test_targeted_research_is_bounded_and_cannot_drop_questions(self):
        from pipeline import research_requirements as research
        from types import SimpleNamespace
        plan={'items':[{'id':'q1','question':'必要な返金条件','required':True}]}
        pages={'content_text':json.dumps([{'url':'https://official.test','status':'success','text':'料金のみ'}])}
        artifacts={'fresh_sources':pages,'fact_sheet':{'content_text':'料金のみ'}}
        msg=SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps({'items':[
            {'id':'q1','status':'unresearched','answer':'','evidence':[],'reason':'規約未取得'}]}))],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
        with patch.object(research,'load_plan',return_value=plan),patch.object(research,'get_artifact',side_effect=lambda _,s:artifacts[s]), \
             patch.object(research,'create_with_retry',return_value=msg),patch.object(research,'upsert_artifact',side_effect=lambda **kw:kw) as save, \
             patch('pipeline.step_fact_sheet.run') as fetch:
            with self.assertRaises(q.ContentQualityError):research.verify('job','比較')
        self.assertEqual(fetch.call_count,2)
        self.assertIn('必要な返金条件',fetch.call_args.kwargs['research_gaps'])
        self.assertFalse(save.call_args.kwargs['meta']['valid'])

    def test_matrix_changes_in_sources_facts_and_plan_invalidate_readiness(self):
        from pipeline import research_requirements as research
        plan={'items':[{'id':'q1','question':'返金条件','required':True}]}
        pages={'content_text':json.dumps([{'url':'https://official.test','status':'success','text':'返金不可'}])}
        matrix={'valid':True,'policy_sha256':research.matrix_policy(),'items':[{'id':'q1','status':'confirmed','answer':'返金不可','evidence':[{'url':'https://official.test','quote':'返金不可'}]}],
                'plan_sha256':q.digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
                'sources_sha256':q.digest(q.source_evidence(pages)),'facts_sha256':q.digest('確認済み')}
        artifacts={'research_matrix':{'content_text':json.dumps(matrix)},'fresh_sources':pages,'fact_sheet':{'content_text':'確認済み'}}
        with patch.object(research,'load_plan',return_value=plan),patch.object(research,'get_artifact',side_effect=lambda _,s:artifacts[s]):
            self.assertTrue(research.require_matrix('job')['valid'])
            with patch.object(research,'MATRIX_SYSTEM','変更後の判定方針'):
                with self.assertRaises(q.ContentQualityError):research.require_matrix('job')
            artifacts['fact_sheet']['content_text']='変更済み'
            with self.assertRaises(q.ContentQualityError):research.require_matrix('job')
            artifacts['fact_sheet']['content_text']='確認済み';plan['items'][0]['question']='新たな質問'
            with self.assertRaises(q.ContentQualityError):research.require_matrix('job')

    def test_h3_short_intro_with_complete_h4_is_not_an_empty_section(self):
        from pipeline.article_quality import validate_delivery
        outline='### H2：比較\n#### H3：料金'
        text='## 比較\n比較します。\n### 料金\n支払額を比較します。\n#### 支払額\n'+'具体的な料金条件。'*10
        self.assertNotIn('empty_section',[i['key'] for i in validate_delivery(text,outline)])
