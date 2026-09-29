import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import step_article, step_outline, step_review, step_final_validate, step_structure_guard
from pipeline.article_quality import parse_length_budget, validate_delivery, select_outline
from pipeline.content_quality import CHECKS, POLICY_VERSION, ContentQualityError, snapshot, requirements_for


OUTLINE = '''### H2：おすすめサービス比較
#### H3：サービスAの特徴
#### H3：サービスBの特徴
#### H3：サービスCの特徴
### H2：選び方
#### H3：目的の確認
### H2：まとめ
'''
CONTRACT = {'required_sections': []}


def complete_article():
    return '\n\n'.join([
        '## おすすめサービス比較', '候補ごとの条件や機能の違いを確認して選びます。' * 5,
        '### サービスAの特徴', 'サービスAの説明には確認済みの情報を使用します。' * 8,
        '### サービスBの特徴', 'サービスBの説明には確認済みの情報を使用します。' * 8,
        '### サービスCの特徴', 'サービスCの説明には確認済みの情報を使用します。' * 8,
        '## 選び方', '条件や目的に沿って候補を比較して選びます。' * 5,
        '### 目的の確認', '利用目的に沿って必要な機能を確認します。' * 8,
        '## まとめ', '必要な機能と利用条件を確認したうえで候補を選びます。' * 5,
    ])


class ArticleQualityTests(unittest.TestCase):
    def test_budget_distinguishes_target_cap_range_and_relative(self):
        self.assertEqual(parse_length_budget('15,123字（上限16,383字）').target, 15123)
        self.assertEqual(parse_length_budget('5,000〜7,000字').target, 6000)
        self.assertIsNone(parse_length_budget('競合平均の1.2倍'))
        self.assertIsNone(parse_length_budget(None))

    def test_automatic_target_survives_entire_outline_run(self):
        database = {'word_count_setting': None}
        artifacts = {
            'serp': {'content_text': json.dumps({'competitor_headings': [
                {'fetch_status': 'success', 'word_count': 12000} for _ in range(4)]})},
            'search_intent': {'content_text': ''}, 'fact_sheet': {'content_text': ''},
            'content_contract': {'content_text': json.dumps(CONTRACT)},
        }
        message = SimpleNamespace(content=[SimpleNamespace(text=OUTLINE)], stop_reason='end_turn',
                                  usage=SimpleNamespace(input_tokens=10, output_tokens=8000))
        with patch.object(step_outline, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
             patch.object(step_outline, 'get_job', side_effect=lambda _: dict(database)), \
             patch.object(step_outline, 'update_job_word_count_setting',
                          side_effect=lambda _, value: database.update(word_count_setting=value)), \
             patch.object(step_outline.anthropic, 'Anthropic'), \
             patch.object(step_outline, 'create_with_retry', return_value=message), \
             patch.object(step_outline, 'upsert_artifact', side_effect=lambda **kw: {'id': 'test', **kw}):
            result = step_outline.run('test', 'おすすめ比較')
        rows = step_article._parse_volume_design(result['content_text'])
        self.assertEqual(sum(row[2] for row in rows), 14400)
        self.assertEqual(database['word_count_setting'], '14,400字（上限15,600字）')

    def test_existing_complete_but_wrong_total_is_repaired(self):
        wrong, _ = step_outline.ensure_complete_volume_design(OUTLINE, '5,000字')
        corrected, changed = step_outline.ensure_complete_volume_design(wrong, '15,123字（上限16,383字）')
        self.assertTrue(changed)
        self.assertEqual(sum(r[2] for r in step_article._parse_volume_design(corrected)), 15123)
        self.assertFalse(step_outline.ensure_complete_volume_design(corrected, '15,123字（上限16,383字）')[1])

    def test_comparison_depth_gets_more_budget_than_summary(self):
        text, _ = step_outline.ensure_complete_volume_design(OUTLINE, '5,000字')
        rows = step_article._parse_volume_design(text)
        self.assertGreater(rows[0][2], rows[2][2] * 3)
        self.assertGreaterEqual(rows[0][2], 150 + 3 * 200)

    def test_impossible_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            step_outline.ensure_complete_volume_design(OUTLINE, '500字')

    def test_missing_service_h3_and_internal_notes_are_rejected(self):
        bad = '## おすすめサービス比較\nサービスA、サービスB、サービスCがおすすめです。\n'
        bad += '※前のパートで個別解説が続く想定ですが、指定に従い次のH2から執筆します。\n'
        bad += '| サービスA | 要確認 |\n'
        keys = {i['key'] for i in validate_delivery(bad, OUTLINE, '15,123字')}
        self.assertTrue({'missing_heading', 'internal_note', 'unfinished_table', 'article_too_short'} <= keys)

    def test_child_under_wrong_parent_does_not_count(self):
        text = complete_article().replace('### サービスAの特徴', '### 別の特徴')
        text += '\n### サービスAの特徴\n' + '確認済みの詳しい説明です。' * 20
        issues = validate_delivery(text, OUTLINE)
        self.assertTrue(any(i.get('title') == 'サービスAの特徴' for i in issues))

    def test_complete_article_with_legitimate_footnote_passes(self):
        article = complete_article() + '\n※料金は確認時点のものです。\n'
        self.assertEqual(validate_delivery(article, OUTLINE), [])

    def test_empty_heading_is_not_completion(self):
        article = complete_article().replace('サービスAの説明には確認済みの情報を使用します。' * 8, '')
        self.assertIn('empty_section', {i['key'] for i in validate_delivery(article, OUTLINE)})

    def test_part_repair_replaces_incomplete_text_before_return(self):
        sections = [('おすすめサービス比較', 5, 700)]
        wanted = select_outline(OUTLINE, [sections[0][0]])
        good = complete_article().split('## 選び方')[0]
        with patch.object(step_article, '_call', side_effect=[('## おすすめサービス比較\n未完成', 10, 20), (good, 30, 40)]) as call:
            text, ti, to = step_article._write_complete_part(None, [{'role': 'user', 'content': '執筆'}], 2000, OUTLINE, sections, 1)
        self.assertEqual(text, good.strip())
        self.assertEqual((ti, to), (40, 60))
        self.assertEqual(call.call_count, 2)
        self.assertEqual(validate_delivery(text, wanted), [])

    def test_part_stops_after_failed_repair(self):
        with patch.object(step_article, '_call', return_value=('## おすすめサービス比較\n未完成', 1, 1)) as call:
            with self.assertRaisesRegex(ValueError, '未完成'):
                step_article._write_complete_part(None, [{'role': 'user', 'content': '執筆'}], 2000, OUTLINE,
                                                  [('おすすめサービス比較', 5, 700)], 1)
        self.assertEqual(call.call_count, 2)

    def test_truncated_writer_response_is_rejected(self):
        response = SimpleNamespace(stop_reason='max_tokens')
        with patch.object(step_article, 'create_with_retry', return_value=response):
            with self.assertRaisesRegex(ValueError, '出力上限'):
                step_article._call(None, [])

    def test_review_requests_missing_content_not_padding(self):
        action = step_review._word_count_action(7604, '15,123字（上限16,383字）')
        self.assertIn('補完', action)
        self.assertIn('水増しは禁止', action)
        self.assertIsNotNone(step_review._word_count_action(1, '15,123字'))
        self.assertIsNone(step_review._word_count_action(15000, '15,123字（上限16,383字）'))

    def test_final_gate_records_failure_and_raises_even_if_old_guard_passes(self):
        artifacts = {'article': {'content_text': '## 比較\nサービスA、B、C'},
                     'content_contract': {'content_text': json.dumps(CONTRACT)},
                     'outline': {'content_text': OUTLINE}}
        with patch.object(step_final_validate, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
             patch.object(step_final_validate, 'get_job', return_value={'word_count_setting': '15,123字'}), \
             patch.object(step_final_validate, 'upsert_artifact', side_effect=lambda **kw: kw) as save:
            with self.assertRaises(ValueError):
                step_final_validate.run('test', '比較')
        self.assertFalse(json.loads(save.call_args.kwargs['content_text'])['valid'])

    def test_structure_guard_repairs_budget_even_without_structure_violation(self):
        wrong, _ = step_outline.ensure_complete_volume_design(OUTLINE, '5,000字')
        artifacts = {'outline': {'content_text': wrong}, 'content_contract': {'content_text': json.dumps(CONTRACT)}}
        with patch.object(step_structure_guard, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
             patch.object(step_structure_guard, 'get_job', return_value={'word_count_setting': '15,123字'}), \
             patch.object(step_structure_guard, 'upsert_artifact', side_effect=lambda **kw: kw) as save:
            step_structure_guard.run('test', '比較')
        outline_save = next(c.kwargs for c in save.call_args_list if c.kwargs['step'] == 'outline')
        self.assertEqual(sum(r[2] for r in step_article._parse_volume_design(outline_save['content_text'])), 15123)

    def test_part_split_balances_large_comparison_without_splitting_h2(self):
        sizes = [1328, 4086, 2316, 541, 1724, 1724, 1133, 541, 1730]
        sections = [(f'章{i}', 4, size) for i, size in enumerate(sizes)]
        parts = step_article._split_sections_into_parts(sections)
        self.assertEqual([sum(s[2] for s in p) for p in parts], [5414, 4581, 5128])
        self.assertEqual([s for p in parts for s in p], sections)

    def test_outline_truncation_retries_once_and_never_saves_partial(self):
        artifacts = {'serp': {'content_text': '{}'}, 'search_intent': {'content_text': ''},
                     'fact_sheet': {'content_text': ''}, 'content_contract': {'content_text': json.dumps(CONTRACT)}}
        def response(reason):
            return SimpleNamespace(content=[SimpleNamespace(text=OUTLINE)], stop_reason=reason,
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=20))
        for second_reason in ('end_turn', 'max_tokens'):
            with self.subTest(second_reason=second_reason), \
                 patch.object(step_outline, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
                 patch.object(step_outline, 'get_job', return_value={'word_count_setting': '5,000字'}), \
                 patch.object(step_outline.anthropic, 'Anthropic'), \
                 patch.object(step_outline, 'create_with_retry', side_effect=[response('max_tokens'), response(second_reason)]) as model, \
                 patch.object(step_outline, 'upsert_artifact', side_effect=lambda **kw: {'id': 'test', **kw}) as save:
                if second_reason == 'max_tokens':
                    with self.assertRaisesRegex(ValueError, '未完成'):
                        step_outline.run('test', '比較')
                    save.assert_not_called()
                else:
                    result = step_outline.run('test', '比較')
                    self.assertEqual(result['meta']['output_tokens'], 40)
                self.assertEqual(model.call_count, 2)
                self.assertEqual(model.call_args.kwargs['max_tokens'], step_outline.MAX_TOKENS * 2)

    def test_review_cannot_replace_complete_article_with_missing_sections(self):
        good = complete_article()
        artifacts = {'article': {'content_text': good, 'meta': {}}, 'outline': {'content_text': OUTLINE},
                     'fact_sheet': {'content_text': ''}, 'content_contract': {'content_text': json.dumps(CONTRACT)}}
        response = SimpleNamespace(
            content=[SimpleNamespace(text='===ARTICLE_START===\n## おすすめサービス比較\n省略\n===ARTICLE_END===\n===SUMMARY_START===\n編集済み\n===SUMMARY_END===')],
            stop_reason='end_turn', usage=SimpleNamespace(input_tokens=10, output_tokens=20))
        with patch.object(step_review, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
             patch.object(step_review, 'get_job', return_value={'word_count_setting': '1,000字'}), \
             patch.object(step_review.anthropic, 'Anthropic'), \
             patch.object(step_review, 'create_with_retry', return_value=response) as model, \
             patch.object(step_review, 'upsert_artifact', side_effect=lambda **kw: {'id': 'test', **kw}) as save:
            with self.assertRaises(ContentQualityError):
                step_review.run('test', '比較')
        result = next(c.kwargs for c in save.call_args_list if c.kwargs['step'] == 'article')
        self.assertEqual(result['content_text'], good)
        self.assertFalse(result['meta']['reviewed'])
        self.assertIn(OUTLINE, model.call_args.kwargs['messages'][0]['content'])

    def test_complete_article_passes_final_gate(self):
        job = {'word_count_setting': '1,000字'}
        audit = {'checks': [{'key': k, 'status': 'pass', 'reason': '検証済み'} for k in CHECKS],
                 'valid': True, 'policy_version': POLICY_VERSION,
                 'snapshot': snapshot(complete_article(), '', OUTLINE, CONTRACT, requirements_for(job, '比較'))}
        artifacts = {'article': {'content_text': complete_article()}, 'outline': {'content_text': OUTLINE},
                     'fact_sheet': {'content_text': ''}, 'content_audit': {'content_text': json.dumps(audit)},
                     'content_contract': {'content_text': json.dumps(CONTRACT)}}
        with patch.object(step_final_validate, 'get_artifact', side_effect=lambda _, step: artifacts[step]), \
             patch.object(step_final_validate, 'get_job', return_value={'word_count_setting': '1,000字'}), \
             patch.object(step_final_validate, 'upsert_artifact', side_effect=lambda **kw: kw):
            result = step_final_validate.run('test', '比較')
        self.assertTrue(result['meta']['valid'])


if __name__ == '__main__':
    unittest.main()
