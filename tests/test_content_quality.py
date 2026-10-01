import json

SOURCE = {"content_text": json.dumps([{"url": "https://official.example/", "status": "success", "text": "直接取得した原文です"}])}
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import content_quality as quality
from pipeline import step_content_audit, step_research_guard, step_final_validate, step_article
from pipeline.article_quality import validate_promised_comparison_count
from pipeline.autofix import classify_error
from pipeline.step_plan import build_step_plan


def report(failed=None):
    return {'checks': [{'key': k, 'status': 'fail' if k == failed else 'pass',
                       'reason': '比較条件を確認'} for k in quality.CHECKS], 'valid': not failed}


class QualityTests(unittest.TestCase):
    def test_only_confirmed_paragraphs_reach_writer(self):
        source = '''# 調査
未確認の要約は9,999円です。

## サービスA
> **料金**：1ヶ月500円。
>
> 条件は男性の基本プラン。
> 出典：https://example.com/price 確認箇所：「基本プランは500円です」 [confirmed]

> サービスAは無料 [hypothesis]

## サービスB
> 月999円 [confirmed]、会員100万人 [hypothesis]

## 未調査
これは調べていない説明です。
'''
        clean = quality.confirmed_facts(source)
        self.assertIn('1ヶ月500円', clean)
        self.assertIn('男性の基本プラン', clean)
        for excluded in ('9,999', 'は無料', 'サービスB', '999円', '未調査'):
            self.assertNotIn(excluded, clean)

    def test_mixed_or_untagged_paragraphs_are_not_confirmed(self):
        self.assertEqual(quality.confirmed_facts('数字123\n\n[confirmed] 値2 [hypothesis]'), '')

    def test_preserves_style_policy_with_fact_constraint(self):
        prompt = step_article._build_chains_prompt([{'concrete_phrase': '月1万円は高い'}])
        self.assertIn('原文を活かしてよい', prompt)
        self.assertIn('確認済み情報と一致', prompt)
        self.assertNotIn('原文の転記や各H2末尾への挿入はしない', prompt)

    def test_all_checks_required_and_verdict_is_computed(self):
        self.assertTrue(quality.parse_audit(json.dumps(report()))['valid'])
        bad = report('comparison_conditions');bad['valid'] = True
        self.assertFalse(quality.parse_audit(json.dumps(bad))['valid'])
        for invalid in ({'checks': []}, {'checks': report()['checks'][:-1]}, [], {'checks': None}):
            with self.assertRaises(quality.ContentQualityError):
                quality.parse_audit(json.dumps(invalid))
        bad = report();bad['checks'][0]['status'] = 'not_applicable'
        with self.assertRaises(quality.ContentQualityError):
            quality.parse_audit(json.dumps(bad))

    def test_truncated_audit_and_thinking_before_text(self):
        msg = SimpleNamespace(stop_reason='end_turn', content=[
            SimpleNamespace(type='thinking'), SimpleNamespace(type='text', text='final')])
        self.assertEqual(quality.response_text(msg), 'final')
        msg.stop_reason = 'max_tokens'
        with self.assertRaises(quality.ContentQualityError):quality.response_text(msg)

    def test_stale_audit_fails_on_content_evidence_or_requirements_changes(self):
        params = ['article', 'facts', 'outline', {'required': 2}, {'keyword': 'test'}]
        approved = {**report(), 'policy_version': quality.POLICY_VERSION, 'snapshot': quality.snapshot(*params)}
        quality.require_audit(approved, quality.snapshot(*params))
        for index in range(len(params)):
            changed = list(params);changed[index] = 'changed'
            with self.assertRaises(quality.ContentQualityError):
                quality.require_audit(approved, quality.snapshot(*changed))

    def test_eight_selections_with_three_rows_rejected_but_tips_not_counted(self):
        text = '# アプリ8選\n## アプリ8選を比較\n| サービス名 | 料金 |\n|---|---|\n'
        text += '\n'.join(f'| サービス{i} | 無料 |' for i in range(3))
        self.assertTrue(validate_promised_comparison_count(text))
        text += '\n' + '\n'.join(f'| サービス{i} | 無料 |' for i in range(3, 8))
        self.assertEqual(validate_promised_comparison_count(text), [])
        self.assertEqual(validate_promised_comparison_count('# 便利なコツ8選\n## 使い方\n本文'), [])

    def test_two_named_services_are_not_forced_to_three(self):
        text = '# サービス2選\n| サービス名 | 料金 |\n|---|---|\n| A | 100円 |\n| B | 200円 |'
        self.assertEqual(validate_promised_comparison_count(text), [])

    def test_standard_mode_requires_readiness_and_final_audit(self):
        for high in (False, True):
            keys = [k for k, _ in build_step_plan({'high_accuracy_mode': high})]
            self.assertLess(keys.index('research_validation'), keys.index('article'))
            self.assertLess(keys.index('review'), keys.index('content_audit'))
            if high:self.assertLess(keys.index('fact_review'), keys.index('content_audit'))
            self.assertEqual(keys[-2:], ['content_audit', 'final_structure_validation'])

    def test_quality_failures_do_not_retry_whole_pipeline_or_create_bug_issue(self):
        diagnosis = classify_error(quality.ContentQualityError('未確認'))
        self.assertFalse(diagnosis['retryable'])
        self.assertEqual(diagnosis['type'], 'operational')

    def test_final_gate_rejects_missing_semantic_audit_even_when_structure_passes(self):
        artifacts = {'article': {'content_text': '## 比較\n十分な説明。'},
                     'outline': {'content_text': '### H2：比較'},
                     'content_contract': {'content_text': '{"required_sections":[]}'},
                     'fresh_sources': SOURCE, 'fact_sheet': {'content_text': ''}}
        with patch.object(step_final_validate, 'get_artifact', side_effect=lambda _, s: artifacts[s]), \
             patch.object(step_final_validate, 'get_job', return_value={}), \
             patch.object(step_final_validate, 'upsert_artifact', side_effect=lambda **kw: kw) as save:
            with self.assertRaises(quality.ContentQualityError):step_final_validate.run('j', '比較')
        self.assertIn('content_audit_not_passed', save.call_args.kwargs['content_text'])

    def test_repair_is_bounded_and_every_candidate_is_reaudited(self):
        artifacts = {'article': {'content_text': '## 比較\n十分な説明。'},
                     'outline': {'content_text': '### H2：比較'},
                     'content_contract': {'content_text': '{"required_sections":[]}'},
                     'fresh_sources': SOURCE, 'fact_sheet': {'content_text': '> 確認済みの事実 [confirmed]'}}
        response = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(text='## 比較\n修正した説明。')])
        with patch.object(step_content_audit, 'get_artifact', side_effect=lambda _, s: artifacts[s]), \
             patch.object(step_content_audit, 'get_job', return_value={}), \
             patch.object(step_content_audit.anthropic, 'Anthropic'), \
             patch.object(step_content_audit, 'audit', side_effect=lambda *a, **kw: report('conclusion_consistency')) as audit, \
             patch.object(step_content_audit, 'create_with_retry', return_value=response) as repair, \
             patch.object(step_content_audit, 'upsert_artifact', side_effect=lambda **kw: kw) as save:
            with self.assertRaises(quality.ContentQualityError):step_content_audit.run('j', '比較')
        self.assertEqual(audit.call_count, 3)
        self.assertEqual(repair.call_count, 2)
        self.assertEqual(audit.call_args.kwargs['text'], '## 比較\n修正した説明。')
        self.assertFalse(json.loads([c.kwargs for c in save.call_args_list if c.kwargs['step'] == 'content_audit'][-1]['content_text'])['valid'])

    def test_missing_research_reruns_retrieval_once_and_rejects_persistent_gap(self):
        from pipeline import step_fact_sheet, step_content_contract, step_outline, step_structure_guard
        artifacts = {'outline': {'content_text': '### H2：比較8選'}, 'fresh_sources': SOURCE, 'fact_sheet': {'content_text': ''},
                     'content_contract': {'content_text': '{}'}}
        with patch.object(step_research_guard, 'get_artifact', side_effect=lambda _, s: artifacts[s]), \
             patch.object(step_research_guard, 'get_job', return_value={}), \
             patch.object(step_research_guard.anthropic, 'Anthropic'), \
             patch.object(step_research_guard, 'audit', side_effect=lambda *a, **kw: report('coverage')) as audit, \
             patch.object(step_research_guard, 'upsert_artifact', side_effect=lambda **kw: kw), \
             patch.object(step_fact_sheet, 'run') as research, \
             patch.object(step_content_contract, 'run'), patch.object(step_outline, 'run'), \
             patch.object(step_structure_guard, 'run'):
            with self.assertRaises(quality.ContentQualityError):step_research_guard.run('j', '比較')
        self.assertEqual(audit.call_count, 3)
        self.assertEqual(research.call_count, 1)
        self.assertIn('research_gaps', research.call_args.kwargs)


    def test_outline_condition_error_can_recover_without_repeating_source_search(self):
        from pipeline import step_fact_sheet, step_content_contract, step_outline, step_structure_guard
        artifacts={'outline':{'content_text':'### H2：比較'},'fact_sheet':{'content_text':''},
                   'fresh_sources':SOURCE,'content_contract':{'content_text':'{}'}}
        with patch.object(step_research_guard,'get_artifact',side_effect=lambda _,s:artifacts[s]), \
             patch.object(step_research_guard,'get_job',return_value={}), \
             patch.object(step_research_guard.anthropic,'Anthropic'), \
             patch.object(step_research_guard,'audit',side_effect=[report('comparison_conditions'),report()]), \
             patch.object(step_research_guard,'upsert_artifact',side_effect=lambda **kw:kw), \
             patch.object(step_fact_sheet,'run') as research, \
             patch.object(step_content_contract,'run'),patch.object(step_outline,'run') as outline, \
             patch.object(step_structure_guard,'run_before_research'):
            result=step_research_guard.run('j','比較')
        research.assert_not_called()
        outline.assert_called_once()
        self.assertIn('comparison_conditions',outline.call_args.kwargs['research_gaps'])
        self.assertTrue(result['meta']['valid'])

    def test_structure_failure_routes_to_research_without_becoming_pass(self):
        from pipeline import step_structure_guard
        with patch.object(step_structure_guard, 'run', side_effect=quality.ContentQualityError('比較対象が不足')), \
             patch.object(step_structure_guard, 'upsert_artifact', side_effect=lambda **kw:kw):
            result=step_structure_guard.run_before_research('j','比較')
        self.assertFalse(result['meta']['valid'])
        self.assertTrue(json.loads(result['content_text'])['needs_research'])

    def test_mechanical_outline_gap_cannot_pass_even_if_model_approves(self):
        from pipeline import step_fact_sheet, step_content_contract, step_outline, step_structure_guard
        artifacts={'outline':{'content_text':'### H2：比較'},'fact_sheet':{'content_text':''},
                   'fresh_sources':SOURCE,'content_contract':{'content_text':'{}'}}
        with patch.object(step_research_guard,'get_artifact',side_effect=lambda _,s:artifacts[s]), \
             patch.object(step_research_guard,'get_job',return_value={}), \
             patch.object(step_research_guard.anthropic,'Anthropic'), \
             patch.object(step_research_guard,'audit',side_effect=lambda *a,**kw:report()), \
             patch.object(step_research_guard,'upsert_artifact',side_effect=lambda **kw:kw) as save, \
             patch.object(step_fact_sheet,'run') as research, \
             patch.object(step_content_contract,'run'),patch.object(step_outline,'run'), \
             patch.object(step_structure_guard,'run_before_research'), \
             patch.object(step_structure_guard,'validate_structure',return_value=[{'key':'named_service_comparison','reason':'不足'}]):
            with self.assertRaises(quality.ContentQualityError):step_research_guard.run('j','比較')
        self.assertEqual(research.call_count,1)
        self.assertFalse(save.call_args.kwargs['meta']['valid'])
        checks=json.loads(save.call_args.kwargs['content_text'])['checks']
        self.assertEqual(next(c for c in checks if c['key']=='coverage')['status'],'fail')


if __name__ == '__main__':unittest.main()
