"""Behavioral proof of scoped recovery, final gates, and durable no-replay state."""
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from pipeline import editorial_recovery as recovery, content_quality as cq
from pipeline import step_content_audit, quality_warnings, quality_budget
from pipeline.content_edits import content_blocks
from pipeline.section_identity import bind_sections
from pipeline.focused_quality import ROLES
from test_editorial_blueprint import fixture, artifact


def materials():
    _, _, _, ctx, value = fixture()
    child = copy.deepcopy(value['sections'][0])
    child.update(id='s02', parent_id='s01', level=3, title='始める前の準備', reader_progression=None,
                 budget={'chars': 0, 'importance': 0, 'reason': ''})
    other = copy.deepcopy(value['sections'][0]); other.update(id='s03', title='別の判断')
    value['sections'] += [child, other]
    outline = artifact(value, ctx)
    text = '# 記事\n\n読者への入口。\n\n## 閲覧と送信の利用条件\n\n用途で選びます。\n\n### 始める前の準備\n\n希望を整理します。\n## 別の判断\n\n別の説明です。'
    article = {'content_text': text, 'meta': {'section_map': bind_sections(text, outline['content_text'])}}
    target = next(b['id'] for b in content_blocks(text) if b['text'].startswith('希望を整理'))
    return article, outline, {'valid': False, 'editorial_findings': [
        {'check_key': 'prose_quality', 'severity': 'blocking', 'stage_to_fix': 'outline',
         'block_ids': [target], 'problem': '方法と準備が同階層', 'reader_impact': '問いの区切りが不明瞭',
         'required_change': '準備を独立した章へ移す'}]}


def passed(context):
    from heading_review_fixture import reviews
    h = cq.snapshot(**context)
    check = lambda k: {'key': k, 'status': 'pass', 'reason': '合成した検証応答', 'affected_blocks': []}
    return {'valid': True, 'stage': 'article', 'snapshot': h, 'policy_version': cq.POLICY_VERSION,
            'checks': [check(k) for k in cq.CHECKS],
            'phases': {role: {'snapshot': h, 'checks': [check(k) for k in keys],
                **({'heading_answer_reviews': reviews(context['text'])} if role == 'organization' else {})}
                for role, keys in ROLES.items()}}


class RecoveryMappingTests(unittest.TestCase):
    def test_real_input_signature_survives_planned_outline_change_but_final_binding_does_not(self):
        article,outline,_=materials()
        records={'article':article,'outline':outline,'fact_sheet':{'content_text':'根拠A'}}
        job={'id':'job','article_purpose':'information'}
        with patch.object(recovery,'get_job',return_value=job), \
             patch.object(recovery,'get_optional_artifact',side_effect=lambda jid,key:records.get(key)), \
             patch('pipeline.db.get_optional_artifact',side_effect=lambda jid,key:records.get(key)), \
             patch('pipeline.research_requirements.load_plan',return_value={}):
            before=recovery.input_signature('job','比較')
            self.assertIn('article_design',cq.final_review_requirements(job,'比較'))
            # A real binding becomes stale between outline repair and writing.
            revised=copy.deepcopy(outline['meta']['editorial_blueprint'])
            revised['sections'][1]['title']='変更した準備の説明'
            records['outline']=artifact(revised,outline['meta']['blueprint_context'])
            self.assertEqual(recovery.input_signature('job','比較'),before)
            with self.assertRaisesRegex(cq.ContentQualityError,'読者QA'):cq.final_review_requirements(job,'比較')
            records['fact_sheet']['content_text']='根拠B'
            self.assertNotEqual(recovery.input_signature('job','比較'),before)

    def test_maps_child_and_actual_parent_without_next_heading_in_same_block(self):
        article, outline, report = materials()
        result = recovery.outline_report(article, outline, report)
        self.assertEqual(result['blueprint_issues'][0]['section_ids'], ['s01', 's02'])
        self.assertEqual(result['checks'][0]['affected_blocks'], [])
        self.assertIn('独立した章', result['checks'][0]['reason'])
        self.assertNotIn('block-', json.dumps(result))

    def test_unknown_lead_and_stale_addresses_never_expand_to_all_sections(self):
        for change in ('unknown', 'lead', 'stale'):
            article, outline, report = materials()
            if change == 'unknown': report['editorial_findings'][0]['block_ids'] = ['block-9999']
            if change == 'lead': report['editorial_findings'][0]['block_ids'] = ['block-0002']
            if change == 'stale': article['content_text'] = article['content_text'].replace('### 始める前の準備', '### 未対応の別見出し')
            with self.subTest(change=change), self.assertRaises(recovery.EditorialRecoveryError):
                recovery.outline_report(article, outline, report)

    def test_source_issue_is_not_repaired_by_rewriting_outline(self):
        article, outline, report = materials()
        report['editorial_findings'][0]['stage_to_fix'] = 'research'
        with self.assertRaisesRegex(recovery.EditorialRecoveryError, '資料不足'):
            recovery.outline_report(article, outline, report)

    def test_advisory_research_finding_does_not_hide_explicit_outline_fix(self):
        article, outline, report = materials()
        row = copy.deepcopy(report['editorial_findings'][0]); row.update(severity='improvement', stage_to_fix='research')
        report['editorial_findings'].append(row)
        self.assertEqual(recovery.outline_report(article, outline, report)['blueprint_issues'][0]['section_ids'], ['s01', 's02'])


class RecoveryExecutionTests(unittest.TestCase):
    def harness(self, stack):
        article, outline, report = materials()
        records = {'article': article, 'outline': outline, 'content_audit': {'content_text': '{}'}}
        def get(job, key): return copy.deepcopy(records[key])
        def optional(job, key): return copy.deepcopy(records.get(key))
        def save(**kw): records[kw['step']] = copy.deepcopy(kw); return copy.deepcopy(kw)
        for name, fn in [('get_artifact', get), ('get_optional_artifact', optional), ('upsert_artifact', save)]:
            stack.enter_context(patch.object(recovery, name, side_effect=fn))
        stack.enter_context(patch.object(recovery, 'get_job', return_value={'delivery_type': 'full'}))
        def context(*args):
            return dict(text=records['article']['content_text'], facts='', outline=records['outline']['content_text'],
                        contract={}, requirements={}, sources='')
        stack.enter_context(patch.object(recovery, 'audit_context', side_effect=context))
        signature = stack.enter_context(patch.object(recovery, 'input_signature', return_value='unchanged-inputs'))
        report.update(snapshot=cq.snapshot(**context()))
        records['content_audit']['content_text'] = json.dumps(report)
        events = []
        def stage(key):
            def run(*args, **kw):
                self.assertFalse(quality_warnings.enabled())
                self.assertEqual(quality_budget.STAGE.get(), 'research_validation' if key == 'outline_repair' else key)
                events.append(key)
                if key == 'outline_repair': records['outline']['content_text'] += '\n修正した構成'
                if key == 'article': records['article']['content_text'] = '# 新しい記事\n\n新しい構成から執筆した原稿。'
                if key == 'content_audit': records['content_audit'] = {'content_text': json.dumps(passed(context()))}
            return run
        keys = ['outline_repair', 'structure_guard', 'research_validation', 'service_map', 'article', 'cta_inject', 'content_audit']
        steps = [(k, stage(k)) for k in keys]
        plan = stack.enter_context(patch.object(recovery, 'recovery_steps', return_value=steps))
        audit = stack.enter_context(patch.object(step_content_audit, 'run', side_effect=recovery.UpstreamRepairRequired('upstream', report)))
        folder = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.dict(os.environ, {'QUALITY_BUDGET_DIR': folder}))
        return records, report, events, steps, audit, signature, plan

    def test_pass_has_no_repair_or_research_and_does_not_add_requests(self):
        with ExitStack() as stack:
            records, _, events, _, audit, _, _ = self.harness(stack)
            before = copy.deepcopy(records)
            audit.side_effect = None; audit.return_value = 'normal audit result'
            self.assertEqual(recovery.run('job', 'keyword'), 'normal audit result')
            self.assertEqual(events, []); self.assertEqual(records, before)
            audit.assert_called_once()

    def test_failed_article_maps_repairs_and_rechecks_only_dependent_tail(self):
        with ExitStack() as stack:
            records, _, events, steps, audit, _, _ = self.harness(stack)
            original = copy.deepcopy(records['article'])
            result = recovery.run('job', 'keyword')
            self.assertTrue(json.loads(result['content_text'])['valid'])
            self.assertEqual(events, [k for k, _ in steps])
            state = json.loads(records['editorial_recovery']['content_text'])
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(records['article_before_editorial_recovery']['content_text'], original['content_text'])
            self.assertFalse(json.loads(records['structure_validation_final']['content_text'])['valid'])
            # The normal pipeline's final validator, not recovery's done flag, owns sealing.
            self.assertNotIn('serp', events); self.assertNotIn('fact_sheet', events)
            self.assertNotIn('research_plan', events)
            audit.assert_called_once()

    def test_unchanged_completed_recovery_reuses_verified_result_without_rewriting(self):
        with ExitStack() as stack:
            records, _, events, _, audit, _, _ = self.harness(stack)
            recovery.run('job', 'keyword'); before = copy.deepcopy(records); events.clear()
            result = recovery.run('job', 'keyword')
            self.assertTrue(json.loads(result['content_text'])['valid'])
            self.assertEqual(events, []); self.assertEqual(records, before); audit.assert_called_once()

    def test_new_strict_audit_refreshes_completed_receipt_without_replaying_writer(self):
        with ExitStack() as stack:
            records, _, events, _, audit, _, _ = self.harness(stack)
            recovery.run('job', 'keyword')
            previous = json.loads(records['editorial_recovery']['content_text'])
            records['article']['content_text'] += '\n\n新しい確認対象の説明。'
            text = records['article']['content_text']; events.clear()
            def recheck(*args, **kwargs):
                fresh = passed(recovery.audit_context('job', 'keyword'))
                records['content_audit'] = {'content_text': json.dumps(fresh)}
                return records['content_audit']
            audit.side_effect = recheck
            result = recovery.run('job', 'keyword')
            current = json.loads(records['editorial_recovery']['content_text'])
            self.assertEqual(current['final_snapshot'], json.loads(result['content_text'])['snapshot'])
            self.assertEqual(current['verification_history'], [previous['final_snapshot']])
            self.assertEqual(current['completed_steps'], previous['completed_steps'])
            self.assertEqual(records['article']['content_text'], text)
            self.assertEqual(events, [])
            recovery.run('job', 'keyword')
            self.assertEqual(audit.call_count, 2)

    def test_stale_audit_does_not_refresh_completed_receipt(self):
        with ExitStack() as stack:
            records, _, _, _, audit, _, _ = self.harness(stack)
            recovery.run('job', 'keyword')
            before = copy.deepcopy(records['editorial_recovery'])
            records['article']['content_text'] += '\n\n変更された本文。'
            audit.side_effect = None; audit.return_value = records['content_audit']
            with self.assertRaises(cq.ContentQualityError): recovery.run('job', 'keyword')
            self.assertEqual(records['editorial_recovery'], before)

    def test_completed_save_clears_stale_pending_marker_without_model_replay(self):
        with ExitStack() as stack:
            records, _, events, _, audit, _, _ = self.harness(stack)
            recovery.run('job','keyword')
            self.assertFalse(records['article']['meta']['editorial_recovery_pending'])
            # A save made by the previous version can still have this flag.
            records['article']['meta']['editorial_recovery_pending']=True
            text=records['article']['content_text'];events.clear()
            snapshot=json.loads(records['editorial_recovery']['content_text'])['final_snapshot']
            result=recovery.run('job','keyword')
            self.assertFalse(records['article']['meta']['editorial_recovery_pending'])
            self.assertEqual(records['article']['content_text'],text)
            self.assertEqual(json.loads(result['content_text'])['snapshot'],snapshot)
            self.assertEqual(events,[]);audit.assert_called_once()

    def test_ready_checkpoint_skips_finished_steps_after_process_resume(self):
        with ExitStack() as stack:
            records, report, events, steps, audit, _, _ = self.harness(stack)
            state = recovery.begin('job', 'keyword', report)
            state['completed_steps'] = ['outline_repair', 'structure_guard']
            recovery.save('job', 'editorial_recovery', state)
            recovery.run('job', 'keyword')
            self.assertEqual(events, [k for k, _ in steps][2:]); audit.assert_not_called()

    def test_running_or_failed_checkpoint_is_not_resent_and_retains_draft(self):
        for status in ('running', 'failed'):
            with self.subTest(status=status), ExitStack() as stack:
                records, report, events, _, audit, _, _ = self.harness(stack)
                state = recovery.begin('job', 'keyword', report); state['status'] = status
                recovery.save('job', 'editorial_recovery', state)
                with patch.object(quality_warnings,'has_saved_draft',return_value=True), patch.object(quality_warnings,'mark_review_needed') as mark, patch.object(quality_warnings, 'warn') as warn:
                    quality_warnings.scoped(recovery.run, 'content_audit')('job', 'keyword')
                self.assertEqual(events, []); audit.assert_not_called(); warn.assert_called_once();mark.assert_called_once()

    def test_changed_input_or_article_stops_before_any_step(self):
        for change in ('article', 'outline', 'material'):
            with self.subTest(change=change), ExitStack() as stack:
                records, report, events, _, audit, signature, _ = self.harness(stack)
                recovery.begin('job', 'keyword', report)
                if change == 'material': signature.return_value = 'new-material'
                else: records[change]['content_text'] += '外部変更'
                with self.assertRaises(recovery.EditorialRecoveryError): recovery.run('job', 'keyword')
                self.assertEqual(events, []); audit.assert_not_called()

    def test_native_failure_preserves_stopped_stage_and_never_replays_paid_steps(self):
        with ExitStack() as stack:
            records, _, events, steps, audit, _, plan = self.harness(stack)
            fail = Mock(side_effect=TimeoutError('provider response unknown'))
            plan.return_value = steps[:2] + [('research_validation', fail)] + steps[3:]
            with self.assertRaises(recovery.EditorialRecoveryError): recovery.run('job', 'keyword')
            state = json.loads(records['editorial_recovery']['content_text'])
            self.assertEqual(state['status'], 'failed'); self.assertEqual(state['current_step'], 'research_validation')
            self.assertEqual(events, ['outline_repair', 'structure_guard'])
            with self.assertRaises(recovery.EditorialRecoveryError): recovery.run('job', 'keyword')
            fail.assert_called_once(); audit.assert_called_once()

    def test_missing_final_phase_stays_failed_while_saved_body_is_delivered(self):
        with ExitStack() as stack:
            records, _, events, steps, _, _, plan = self.harness(stack)
            def bad_audit(*args, **kw):
                context = recovery.audit_context('job', 'keyword')
                report = passed(context); report['phases'].pop('organization')
                records['content_audit'] = {'content_text': json.dumps(report)}
            plan.return_value = steps[:-1] + [('content_audit', bad_audit)]
            with patch.object(quality_warnings,'has_saved_draft',return_value=True), patch.object(quality_warnings,'mark_review_needed') as mark, patch.object(quality_warnings, 'warn') as warn:
                quality_warnings.scoped(recovery.run, 'content_audit')('job', 'keyword')
            self.assertEqual(json.loads(records['editorial_recovery']['content_text'])['status'], 'failed')
            self.assertTrue(records['article']['meta']['editorial_recovery_pending'])
            warn.assert_called_once();mark.assert_called_once()

    def test_corrupt_completion_list_does_not_skip_unverified_steps(self):
        with ExitStack() as stack:
            records, report, events, _, audit, _, _ = self.harness(stack)
            state = recovery.begin('job', 'keyword', report)
            state['completed_steps'] = ['article']
            recovery.save('job', 'editorial_recovery', state)
            with self.assertRaisesRegex(recovery.EditorialRecoveryError, '完了記録'): recovery.run('job', 'keyword')
            self.assertEqual(events, []); audit.assert_not_called()

    def test_a_second_upstream_cycle_is_not_started(self):
        with ExitStack() as stack:
            records, report, events, _, audit, _, _ = self.harness(stack)
            recovery.run('job', 'keyword'); events.clear()
            records['article']['content_text'] += '\n新しい手修正'
            with self.assertRaisesRegex(recovery.EditorialRecoveryError, '既に1回'):
                recovery.run('job', 'keyword')
            self.assertEqual(events, []); self.assertEqual(audit.call_count, 2)

    def test_stale_report_cannot_start_any_upstream_call_or_overwrite_article(self):
        with ExitStack() as stack:
            records, report, events, _, audit, _, _ = self.harness(stack)
            before = copy.deepcopy(records); report['snapshot'] = 'old-snapshot'
            audit.side_effect = recovery.UpstreamRepairRequired('upstream', report)
            with self.assertRaises(recovery.EditorialRecoveryError): recovery.run('job', 'keyword')
            self.assertEqual(records, before); self.assertEqual(events, [])

    def test_same_local_job_cannot_run_two_controllers(self):
        with ExitStack() as stack:
            _, _, events, _, audit, _, _ = self.harness(stack)
            path = Path(os.environ['QUALITY_BUDGET_DIR']) / (recovery.digest('job') + '.editorial-recovery.lock')
            with path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(cq.GenerationBusyError, '実行中'): recovery.run('job', 'keyword')
            self.assertEqual(events, []); audit.assert_not_called()

    def test_completed_invalid_patch_can_resume_from_saved_responses_without_replaying_writer(self):
        with ExitStack() as stack:
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled', return_value=True))
            records, report, events, steps, audit, _, _ = self.harness(stack)
            state = recovery.begin('job', 'keyword', report)
            state.update(status='failed', current_step='content_audit', error_kind='patch_validation', error='未適用の修正文の検査失敗')
            state['completed_steps'] = [k for k, _ in steps][:-1]
            report.update(stage='article', attempt=1, policy_version=cq.POLICY_VERSION,
                checks=[{'key': k, 'status': 'fail' if k == 'prose_quality' else 'pass', 'reason': '合成確認', 'affected_blocks': []} for k in cq.CHECKS])
            records['content_audit'] = {'content_text': json.dumps(report)}
            recovery.save('job', 'editorial_recovery', state)
            for n in (0, 1):
                text = '{"edits":[]}'
                records[f'tiered_content_repair_1_{n}'] = {'content_text': text,
                    'meta': {'request_sha256': 'saved-request', 'result_sha256': recovery.digest(text)}}
            stack.enter_context(patch.object(step_content_audit, 'get_artifact', side_effect=lambda j, k: copy.deepcopy(records[k])))
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled', return_value=True))
            recovery.run('job', 'keyword')
            self.assertEqual(events, ['content_audit']); audit.assert_not_called()
            self.assertIn('editorial_recovery_validation_failure_1', records)
            self.assertEqual(json.loads(records['editorial_recovery']['content_text'])['status'], 'completed')

    def test_incomplete_patch_cache_is_never_resent(self):
        with ExitStack() as stack:
            records, report, events, steps, audit, _, _ = self.harness(stack)
            state = recovery.begin('job', 'keyword', report)
            state.update(status='failed', current_step='content_audit', error_kind='patch_validation')
            state['completed_steps'] = [k for k, _ in steps][:-1]
            recovery.save('job', 'editorial_recovery', state)
            stack.enter_context(patch('pipeline.ai.tiered_review_enabled', return_value=True))
            stack.enter_context(patch.object(step_content_audit, 'failed_audit_checkpoint', return_value={'attempt': 1, 'snapshot': 'saved'}))
            with self.assertRaisesRegex(recovery.EditorialRecoveryError, '保存完了'):recovery.run('job', 'keyword')
            self.assertEqual(events, []); audit.assert_not_called()

    def test_interrupted_section_response_resumes_only_its_saved_tail(self):
        for stale in (False, True):
            with ExitStack() as stack:
                records, report, events, steps, audit, _, _ = self.harness(stack)
                state = recovery.begin('job', 'keyword', report)
                keys = [k for k, _ in steps]
                state.update(status='failed', current_step='article', error='incomplete')
                state['completed_steps'] = keys[:keys.index('article')]
                records['tiered_content_repair_outline_sections_0_incomplete'] = {
                    'content_text':'partial', 'meta':{'stop_reason':'max_tokens','request_sha256':'bound-request'}}
                recovery.save('job','editorial_recovery',state)
                if stale:
                    records['article']['content_text'] += '\nchanged'
                    with self.assertRaises(recovery.EditorialRecoveryError): recovery.run('job','keyword')
                    self.assertEqual(events, [])
                else:
                    recovery.run('job','keyword')
                    self.assertEqual(events, ['article','cta_inject','content_audit'])
                    self.assertIn('editorial_recovery_before_suffix_completion',records)
                audit.assert_not_called()

    def test_shared_api_cli_plan_installs_recovery_and_keeps_final_gate(self):
        from pipeline.step_plan import build_step_plan
        steps = build_step_plan({'delivery_type': 'full'})
        run = dict(steps)['content_audit']
        while hasattr(run, '__wrapped__'): run = run.__wrapped__
        self.assertIs(run, recovery.run)
        self.assertEqual(steps[-1][0], 'final_structure_validation')


if __name__ == '__main__': unittest.main()
