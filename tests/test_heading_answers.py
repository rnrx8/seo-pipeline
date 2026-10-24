"""Regression controls for a later answer hiding an unclear chapter opening."""
import copy
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from heading_review_fixture import reviews, with_heading_reviews
from pipeline import heading_answers as ha, focused_quality as fq, content_quality as cq
from pipeline.content_edits import content_blocks


class HeadingAnswerTests(unittest.TestCase):
    text = '''# 記事

誰が何を知りたいかを示すリードです。

## 有料プランの更新と解約手続きの扱い

決済方法によって異なります。

- カードは自動更新です。
- 振込は自動更新されません。

#### 気になる相手がいたら通報・ブロックできる

不審な言動があれば通報し、やり取りを止めたい相手はブロックできます。
'''

    def test_quote_cannot_jump_to_a_later_answer_or_another_section(self):
        value = {'heading_answer_reviews': reviews(self.text)}
        for quote in ('カードは自動更新です。', '不審な言動があれば通報し、やり取りを止めたい相手はブロックできます。'):
            broken = copy.deepcopy(value)
            broken['heading_answer_reviews']['heading-0001']['opening_quote'] = quote
            with self.subTest(quote=quote), self.assertRaises(cq.ContentQualityError):
                ha.checked(broken, self.text)

    def test_individual_failures_override_broad_pass_with_exact_repair_addresses(self):
        value = {'heading_answer_reviews': reviews(self.text)}
        value['heading_answer_reviews']['heading-0001']['opening'].update(status='fail', reason='違いを予告するだけで分かれ方がない')
        value['heading_answer_reviews']['heading-0002']['heading'].update(status='fail', reason='好意と不審の両方に読める')
        findings = ha.checked(value, self.text)
        from pipeline.heading_scope import merge
        result = merge([{'key':'prose_quality','status':'pass','reason':'本文には情報あり','affected_blocks':[]}], findings)
        blocks = {b['id']: b['text'] for b in content_blocks(self.text)}
        self.assertEqual(result[0]['status'], 'fail')
        self.assertEqual([blocks[f['block_ids'][0]] for f in findings], [
            '決済方法によって異なります。', '#### 気になる相手がいたら通報・ブロックできる'])
        self.assertTrue(all(f['stage_to_fix']=='article' for f in findings))

    def test_empty_opening_and_omitted_h4_review_cannot_be_hidden_by_pass(self):
        for text in ('## 親\n\n### 子\n\n子への答え。', self.text):
            value = {'heading_answer_reviews': reviews(text)}
            if text == self.text: value['heading_answer_reviews'].pop('heading-0002')
            with self.subTest(text=text), self.assertRaises(cq.ContentQualityError): ha.checked(value, text)

    def test_own_sentence_in_shared_blocks_and_wrapped_lines_is_exact(self):
        text = '# 題\n## 親\n\n**親への\n答えです。**次の文。\n### 子\n子への答え。\n#### 孫\n\n孫への答え。'
        rows = ha.entries(text)
        self.assertEqual([r['opening_quote'] for r in rows], ['**親への\n答えです。**', '子への答え。', '孫への答え。'])
        self.assertEqual(rows[2]['parents'], ['親', '子'])
        blocks = {b['id']:b['text'] for b in content_blocks(text)}
        self.assertIn('### 子', blocks[rows[1]['opening_block_ids'][0]])

    def test_structural_opening_is_visible_and_code_heading_is_not_reviewed(self):
        text = '# 題\n\n```\n## コード内の見出し\n```\n\n## 手順\n\n- 一覧の項目。\n\n後ろの答え。'
        rows = ha.entries(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['opening_quote'], '- 一覧の項目。')

    def test_protocol_does_not_ban_conditional_answers_or_require_extra_empathy(self):
        text = '# 題\n\n## 解約\n\nカードは自動更新、振込は自動更新なしと、決済方法によって異なります。\n\n### 充電できない場合\n\n充電できない場合は、ケーブルの接続を確認してください。'
        self.assertEqual(ha.checked({'heading_answer_reviews':reviews(text)}, text), [])

    def test_concrete_answer_then_recommendation_in_second_sentence_is_supported(self):
        text = '## 探し方を比較：Aがおすすめ\n\n探し方はA・B・Cの3つです。手軽に始めたい人にはAがおすすめです。'
        row = ha.entries(text)[0]
        self.assertEqual(row['opening_quote'], '探し方はA・B・Cの3つです。')
        self.assertIn('Aがおすすめです。', row['opening_context'])
        self.assertIn('冒頭1〜2文', ha.WRITING_POLICY)
        self.assertEqual(ha.checked({'heading_answer_reviews':reviews(text)},text), [])

    def test_question_inside_quotes_does_not_hide_the_answer_in_same_sentence(self):
        for sentence in ['「どれがいい？」と迷ったら、Aがおすすめです。',
                         '設定例（「これでよい？」と聞かれた場合）はAです。',
                         'ボタン`help?`を押してください。']:
            text = '## 最初に何をするか\n\n' + sentence + '次の説明。'
            with self.subTest(sentence=sentence): self.assertEqual(ha.entries(text)[0]['opening_quote'],sentence)

    def test_old_global_pass_is_rejected_at_strict_final_gate(self):
        from quality_fixtures import phases
        context = dict(text=self.text, facts='', outline='', contract={}, requirements={}, sources='')
        snap = cq.snapshot(**context)
        report = {'valid':True,'stage':'article','snapshot':snap,'policy_version':cq.POLICY_VERSION,
                  'checks':[{'key':k,'status':'pass','reason':'合成応答','affected_blocks':[]} for k in cq.CHECKS],
                  'phases':phases(snap)}
        with self.assertRaisesRegex(cq.ContentQualityError, '第一文'):
            cq._require_audit_strict(report, snap, stage='article', context=context)

    def test_existing_five_calls_store_receipts_and_route_both_failures(self):
        calls = []
        def send(job, step, request):
            role = step.removeprefix('tiered_article_');calls.append(role)
            value = with_heading_reviews({'checks':[{'key':k,'status':'pass','reason':'全体の合成応答','affected_blocks':[]} for k in fq.ROLES[role]]}, request)
            if role == 'organization':
                rows = value['heading_answer_reviews']
                rows['heading-0001']['opening'].update(status='fail',reason='具体的な違いが第一文にない')
                rows['heading-0002']['heading'].update(status='fail',reason='対象が曖昧')
            return value, NS(input_tokens=1,output_tokens=1)
        with patch.dict(os.environ, {'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'legacy'}), patch('pipeline.tiered_research.checked_request',side_effect=send):
            result = fq.audit_article(None,text=self.text,facts='',outline='',contract={},requirements={},sources='')
        self.assertEqual(calls, list(fq.ROLES))
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['phases']['organization']['heading_answer_reviews']), 2)
        self.assertEqual(len(result['editorial_findings']), 2)

    def test_opening_edits_cannot_reuse_a_local_completion_receipt(self):
        from test_prose_completion import CompletionTests
        case = CompletionTests();case.setUp()
        try:
            case.block = next(b['id'] for b in content_blocks(case.text) if b['text']=='持ち運ぶ場面で選びます。')
            base = case.report(case.text)
            from pipeline.prose_completion import eligible
            self.assertFalse(eligible(base,case.text,cq.snapshot(case.text,**case.ctx)))
        finally: case.doCleanups()


if __name__ == '__main__': unittest.main()
