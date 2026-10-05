import unittest
from unittest.mock import patch
from pipeline.article_quality import normalize_outline_headings,outline_sections,select_outline,validate_delivery
from pipeline.step_outline import ensure_complete_volume_design
from pipeline import step_article
from pipeline.content_quality import ContentQualityError

class OutlineNormalizationTests(unittest.TestCase):
    def test_writer_reuses_exact_outline_but_preserves_missing_or_changed_context(self):
        outline='### H2：FAQ\n#### H3：年齢層は？\n固有の対象条件を維持する。'
        article='## FAQ\n概要です。\n### 年齢層は？\n対象条件を説明します。'
        for prior in (outline, outline.replace('対象条件','別の条件'), ''):
            with self.subTest(prior=prior), patch.object(step_article,'_call',return_value=(article,1,1)) as call:
                step_article._write_complete_part(None,[{'role':'user','content':prior+'\n執筆してください。'}],2000,outline,[('FAQ',4,20)],1)
            sent=call.call_args.args[1][0]['content']
            self.assertEqual(sent.count('固有の対象条件を維持する。'),1)
            self.assertIn('#### H3：年齢層は？',sent)

    def test_bold_faq_h2_has_own_children_and_volume_allocation(self):
        outline='### H2：始め方\n本文の指示\n\n### FAQ\n\n**H2：よくある質問**\n\n**H2直下方針**：結論を先に。\n\n#### H3：年齢層は？\n説明\n\n#### H3：アイコンは残る？\n説明'
        normalized,changed=ensure_complete_volume_design(outline,'5,000字')
        self.assertTrue(changed)
        sections=outline_sections(normalized)
        self.assertEqual([(s['level'],s['title'],s['parent']) for s in sections],
                         [(2,'始め方','始め方'),(2,'よくある質問','よくある質問'),(3,'年齢層は？','よくある質問'),(3,'アイコンは残る？','よくある質問')])
        volume=step_article._parse_volume_design(normalized)
        self.assertEqual([v[0] for v in volume],['始め方','よくある質問'])
        self.assertEqual(sum(v[2] for v in volume),5000)
        self.assertNotIn('年齢層は？',select_outline(normalized,['始め方']))
        article='## よくある質問\n回答します。\n### 年齢層は？\n'+'説明です。'*20+'\n### アイコンは残る？\n'+'説明です。'*20
        self.assertEqual(validate_delivery(article,select_outline(normalized,['よくある質問'])),[])
        self.assertEqual(ensure_complete_volume_design(normalized,'5,000字'),(normalized,False))

    def test_metadata_and_code_samples_are_not_promoted(self):
        text='**H2直下方針**：説明\n掲載項目：H3の例\n```\n**H2：コード内の例**\n```\nH3：質問\n'
        result=normalize_outline_headings(text)
        self.assertIn('**H2直下方針**',result)
        self.assertIn('**H2：コード内の例**',result)
        self.assertTrue(result.endswith('#### H3：質問\n'))
        self.assertEqual(normalize_outline_headings(result),result)

    def test_incomplete_part_attempts_are_preserved_for_diagnosis(self):
        outline='### H2：FAQ\n#### H3：年齢層は？'
        with patch.object(step_article,'_call',return_value=('## FAQ\n不完全な本文。',1,1)), \
             patch.object(step_article,'upsert_artifact',side_effect=lambda **kw:kw) as save:
            with self.assertRaises(ContentQualityError):
                step_article._write_complete_part(None,[{'role':'user','content':'執筆'}],2000,outline,[('FAQ',4,400)],3,job_id='j')
        self.assertEqual([c.kwargs['step'] for c in save.call_args_list],['article_part_3_attempt_1','article_part_3_attempt_2'])
        self.assertTrue(all(not c.kwargs['meta']['valid'] for c in save.call_args_list))
        self.assertEqual(save.call_args.kwargs['content_text'],'## FAQ\n不完全な本文。')

    def test_scope_error_is_repaired_in_the_writing_part(self):
        bad='## 無料範囲\n無料会員でできるのは「登録・検索」までです。'
        fixed='## 無料範囲\n男性が無料会員でできるのは「登録・検索」までです。女性は基本機能が無料です。'
        with patch.object(step_article,'_call',side_effect=[(bad,1,1),(fixed,1,1)]) as call, \
             patch.object(step_article,'upsert_artifact',side_effect=lambda **kw:kw) as save:
            result=step_article._write_complete_part(None,[{'role':'user','content':'執筆'}],2000,'### H2：無料範囲',[('無料範囲',4,30)],1,job_id='j',facts='> 女性は基本機能が無料。男性はメッセージが有料。 [confirmed]')
        self.assertEqual(result[0],fixed)
        self.assertEqual(call.call_count,2)
        self.assertFalse(save.call_args_list[0].kwargs['meta']['valid'])
        self.assertTrue(save.call_args_list[1].kwargs['meta']['valid'])
