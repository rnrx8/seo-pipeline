"""One recorded upstream recovery; reuse research, never a prior article pass."""
import copy
import hashlib
import json
import re
import fcntl
import os
import tempfile
from pathlib import Path
from contextvars import ContextVar

from .content_quality import ContentQualityError
from .db import get_artifact, get_optional_artifact, get_job, upsert_artifact

VERSION = 'editorial-recovery-v1'
AUDIT_RECOVERY = ContextVar('editorial_recovery_audit', default=False)


class UpstreamRepairRequired(ContentQualityError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = copy.deepcopy(report)


class EditorialRecoveryError(ContentQualityError):
    """A recovery cannot safely continue; warning policy must not hide it."""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def save(job_id, step, value):
    return upsert_artifact(job_id=job_id, step=step, content_type='application/json',
                           content_text=json.dumps(value, ensure_ascii=False))


def outline_report(article, outline, report):
    """Translate article addresses via stable identities, never as outline IDs."""
    from .content_edits import content_blocks
    from .editorial_blueprint import require_projection
    from .section_identity import valid_binding
    value = require_projection(outline)
    binding = (article.get('meta') or {}).get('section_map')
    text = article['content_text']
    if not value or not valid_binding(text, outline['content_text'], binding):
        raise EditorialRecoveryError('構成へ戻す節の対応が未確認です。原稿と指摘を保持しました。')
    findings = [f for f in report.get('editorial_findings', [])
                if f.get('severity') == 'blocking' and f.get('stage_to_fix') in ('research', 'outline')]
    if not findings or any(f['stage_to_fix'] != 'outline' for f in findings):
        raise EditorialRecoveryError('資料不足の指摘は構成の書換えで解決しません。調査対象の確認が必要です。')
    sections = {s['id']: s for s in value['sections']}
    starts = list(re.finditer(r'^(#{2,4})\s+(.+)$', text, re.M))
    addresses = {}; cursor = 0
    for block in content_blocks(text):
        start = text.index(block['text'], cursor)
        cursor = start + len(block['text'])
        start += len(block['text']) - len(block['text'].lstrip('\r\n'))
        # A paragraph may end with the next heading without a blank line. Its
        # starting section owns it; do not make that unrelated next chapter writable.
        matches = [i for i, heading in enumerate(starts) if heading.start() <= start]
        if matches:
            addresses[block['id']] = binding['entries'][matches[-1]].get('blueprint_id')
    targets = set(); reasons = []
    for finding in findings:
        ids = finding.get('block_ids')
        if (not ids or any(addresses.get(i) not in sections for i in ids)
                or any(not finding.get(k, '').strip() for k in ('problem', 'reader_impact', 'required_change'))):
            raise EditorialRecoveryError('指摘された段落から編集設計の節を特定できません。推測で修正しません。')
        targets.update(addresses[i] for i in ids)
        reasons.append(' / '.join(finding.get(k, '') for k in ('problem', 'reader_impact', 'required_change')))
    # Reparenting a child requires its actual parent, not the next chapter or a
    # guessed section title. The native repair expands only directly linked sections.
    for sid in list(targets):
        parent = sections[sid]['parent_id']
        while parent:
            targets.add(parent)
            parent = sections[parent]['parent_id']
    return {'valid': False, 'stage': 'research',
            'checks': [{'key': 'coverage', 'status': 'fail', 'reason': '\n'.join(reasons), 'affected_blocks': []}],
            'blueprint_issues': [{'repair_kind': 'structure', 'section_ids': sorted(targets), 'reason': '\n'.join(reasons)}],
            'structural_issues': [{'key': 'editorial_heading_scope', 'reason': '\n'.join(reasons)}]}


def audit_context(job_id, keyword):
    from .content_quality import audit_facts, final_review_requirements, source_evidence
    job = get_job(job_id); article = get_artifact(job_id, 'article')
    sources = [get_artifact(job_id, 'fresh_sources')]
    if job.get('high_accuracy_mode'):
        sources.append(get_artifact(job_id, 'fresh_sources_review'))
    return dict(text=article['content_text'],
                facts=audit_facts(get_artifact(job_id, 'fact_sheet')['content_text'], article,
                    high_accuracy=bool(job.get('high_accuracy_mode')),
                    evidence=get_artifact(job_id, 'fact_review_evidence') if job.get('high_accuracy_mode') else None),
                outline=get_artifact(job_id, 'outline')['content_text'],
                contract=json.loads(get_artifact(job_id, 'content_contract')['content_text']),
                requirements=final_review_requirements(job, keyword), sources=source_evidence(*sources))


def input_signature(job_id, keyword):
    from .content_quality import final_review_requirements, snapshot
    job = get_job(job_id)
    # The outline changes before the old body is rewritten. Material/settings
    # identity must not bind that temporary pair; actual article QA still does.
    requirements = final_review_requirements(job, keyword, include_article_design=False)
    material = {}
    for key in ('research_plan', 'research_matrix', 'fact_sheet', 'fresh_sources',
                'content_contract', 'production_brief', 'product_context',
                'search_intent', 'reader_needs', 'intent_chains', 'reference_structure'):
        artifact = get_optional_artifact(job_id, key)
        material[key] = artifact['content_text'] if artifact else None
    settings = {k: job.get(k) for k in ('delivery_type', 'service_id', 'cta_id', 'high_accuracy_mode', 'word_count_setting')}
    return snapshot('', json.dumps(material, ensure_ascii=False, sort_keys=True), '', settings, requirements)


def current_hashes(job_id):
    result = {key: digest(get_artifact(job_id, key)['content_text']) for key in ('article', 'outline')}
    for key in ('fresh_sources_review', 'fact_review_evidence'):
        artifact = get_optional_artifact(job_id, key)
        result[key] = digest(artifact['content_text']) if artifact else None
    return result


def clear_pending_marker(job_id):
    """Only called after strict same-revision QA, including old completed saves."""
    article = get_artifact(job_id, 'article')
    meta = article.get('meta') or {}
    if meta.get('editorial_recovery_pending'):
        upsert_artifact(job_id=job_id, step='article', content_type=article.get('content_type', 'text/markdown'),
                        content_text=article['content_text'],
                        meta={**meta, 'editorial_recovery_pending': False})


def recovery_steps(job):
    from . import (step_research_guard, step_structure_guard, step_service_map, outline_body_repair,
                   step_cta_inject, step_review, step_fact_review, step_content_audit)
    from .ai import astra_review_enabled
    import anthropic
    def repair(job_id, keyword, api_key=None):
        state = json.loads(get_artifact(job_id, 'editorial_recovery')['content_text'])
        client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        return step_research_guard.repair_outline(job_id, keyword, state['outline_report'], client)
    steps = [('outline_repair', repair), ('structure_guard', step_structure_guard.run_before_research),
             ('research_validation', step_research_guard.run)]
    if job.get('service_id') or job.get('cta_id'):
        steps.append(('service_map', step_service_map.run))
    steps.append(('article', outline_body_repair.run))
    if job.get('cta_id'):
        steps.append(('cta_inject', step_cta_inject.run))
    if not astra_review_enabled():
        steps.append(('review', step_review.run))
    if job.get('high_accuracy_mode'):
        steps.append(('fact_review', step_fact_review.run))
    steps.append(('content_audit', step_content_audit.run))
    return steps


def begin(job_id, keyword, report):
    from .content_quality import snapshot
    if report.get('valid') is not False or report.get('evidence_pending'):
        raise EditorialRecoveryError('未確認の照合結果から構成を修正しません。')
    if report.get('snapshot') != snapshot(**audit_context(job_id, keyword)):
        raise EditorialRecoveryError('指摘後に原稿・資料・方針が変更されています。再確認が必要です。')
    article = get_artifact(job_id, 'article'); outline = get_artifact(job_id, 'outline')
    mapped = outline_report(article, outline, report)
    for key in ('article', 'outline', 'content_audit'):
        artifact = get_artifact(job_id, key)
        upsert_artifact(job_id=job_id, step=key + '_before_editorial_recovery',
                        content_type=artifact.get('content_type', 'text/markdown'),
                        content_text=artifact['content_text'], meta=artifact.get('meta') or {})
    state = {'version': VERSION, 'status': 'ready', 'source_snapshot': report['snapshot'],
             'input_signature': input_signature(job_id, keyword), 'expected_hashes': current_hashes(job_id),
             'outline_report': mapped, 'completed_steps': [], 'current_step': None,
             'steps': [key for key, _ in recovery_steps(get_job(job_id))]}
    save(job_id, 'editorial_recovery', state)
    # Keep the failed article readable while invalidating its publication state.
    upsert_artifact(job_id=job_id, step='article', content_type='text/markdown', content_text=article['content_text'],
                    meta={**(article.get('meta') or {}), 'content_audited': False, 'content_audit_snapshot': None,
                          'editorial_recovery_pending': True})
    save(job_id, 'structure_validation_final', {'valid': False, 'status': 'editorial_recovery_pending'})
    return state


def continue_recovery(job_id, keyword, state, api_key=None):
    from .quality_budget import scope
    from .quality_warnings import strict_checks
    retry_patch = (state.get('status') == 'failed' and state.get('error_kind') == 'patch_validation'
                   and state.get('current_step') == 'content_audit')
    partial = get_optional_artifact(job_id, 'tiered_content_repair_outline_sections_0_incomplete')
    retry_suffix = (state.get('status') == 'failed' and state.get('current_step') == 'article'
                    and partial and partial.get('meta', {}).get('stop_reason') == 'max_tokens'
                    and partial['meta'].get('request_sha256'))
    if state.get('version') != VERSION or (state.get('status') != 'ready' and not retry_patch and not retry_suffix):
        raise EditorialRecoveryError('前回の上流修正が実行中または未解決です。結果を確認するまで再送しません。')
    if state['input_signature'] != input_signature(job_id, keyword) or state['expected_hashes'] != current_hashes(job_id):
        raise EditorialRecoveryError('上流修正の保存後に資料・設定・原稿が変更されています。再送しません。')
    steps = recovery_steps(get_job(job_id))
    if state['steps'] != [key for key, _ in steps]:
        raise EditorialRecoveryError('上流修正の実行計画が変更されています。再確認が必要です。')
    completed = state.get('completed_steps')
    if (not isinstance(completed, list) or completed != state['steps'][:len(completed)]
            or (state.get('current_step') is not None and not retry_patch and not retry_suffix)):
        raise EditorialRecoveryError('上流修正の完了記録が不整合です。未確認の工程を省略しません。')
    if retry_suffix:
        # The writer binds the exact request to the incomplete receipt before
        # asking only for missing values. Do not erase the failed run or fee.
        save(job_id, 'editorial_recovery_before_suffix_completion', state)
        state.update(status='ready', current_step=None)
        for field in ('error', 'error_kind', 'error_type'): state.pop(field, None)
        save(job_id, 'editorial_recovery', state)
    if retry_patch:
        from .ai import tiered_review_enabled
        from .content_quality import snapshot
        from .step_content_audit import failed_audit_checkpoint
        if (not tiered_review_enabled() or len(completed) >= len(steps)
                or steps[len(completed)][0] != 'content_audit'):
            raise EditorialRecoveryError('保存応答から再開できる修正範囲ではありません。再送しません。')
        report = failed_audit_checkpoint(job_id, snapshot(**audit_context(job_id, keyword)))
        if not report:
            raise EditorialRecoveryError('元原稿と対応する不合格記録がありません。修正文を再送しません。')
        for n in (0, 1):
            cached = get_optional_artifact(job_id, f'tiered_content_repair_{report["attempt"]}_{n}')
            meta = (cached or {}).get('meta') or {}
            if not cached or not meta.get('request_sha256') or meta.get('result_sha256') != digest(cached['content_text']):
                raise EditorialRecoveryError('修正応答の保存完了を確認できません。未確定の要求は再送しません。')
        failures = state.setdefault('validation_failures', [])
        save(job_id, f'editorial_recovery_validation_failure_{len(failures) + 1}', state)
        failures.append({'error': state.get('error'), 'snapshot': report['snapshot']})
        state.update(status='ready', current_step=None)
        for field in ('error', 'error_kind', 'error_type'):state.pop(field, None)
        save(job_id, 'editorial_recovery', state)
    for key, fn in steps:
        if key in state['completed_steps']:
            continue
        if state['input_signature'] != input_signature(job_id, keyword) or state['expected_hashes'] != current_hashes(job_id):
            raise EditorialRecoveryError('次工程の開始前に資料・原稿の変更を検出しました。古い修正計画を実行しません。')
        state.update(status='running', current_step=key)
        save(job_id, 'editorial_recovery', state)
        try:
            token = AUDIT_RECOVERY.set(key == 'content_audit')
            try:
                with strict_checks(), scope(job_id, 'research_validation' if key == 'outline_repair' else key):
                    fn(job_id, keyword, api_key=api_key)
            finally:
                AUDIT_RECOVERY.reset(token)
            hashes = current_hashes(job_id)
            allowed = {'article': {'article', 'cta_inject', 'review', 'fact_review', 'content_audit'},
                       'outline': {'outline_repair', 'structure_guard', 'research_validation'},
                       'fresh_sources_review': {'fact_review'}, 'fact_review_evidence': {'fact_review'}}
            if (state['input_signature'] != input_signature(job_id, keyword)
                    or any(hashes[k] != previous and key not in allowed[k]
                           for k, previous in state['expected_hashes'].items())):
                raise EditorialRecoveryError('工程の処理範囲外で資料・原稿が変わりました。次の生成を停止します。')
        except Exception as exc:
            state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            from .content_quality import ContentPatchValidationError
            if isinstance(exc, ContentPatchValidationError):state['error_kind'] = 'patch_validation'
            save(job_id, 'editorial_recovery', state)
            raise EditorialRecoveryError('上流修正の ' + key + ' が未完了です。原稿・指摘・費用履歴を保持しました。' + str(exc)) from exc
        state['completed_steps'].append(key)
        state.update(status='ready', current_step=None, expected_hashes=hashes)
        save(job_id, 'editorial_recovery', state)
    from .content_quality import _require_audit_strict, snapshot
    try:
        context = audit_context(job_id, keyword)
        saved = get_artifact(job_id, 'content_audit'); report = json.loads(saved['content_text'])
        _require_audit_strict(report, snapshot(**context), stage='article', context=context)
    except ContentQualityError as exc:
        state.update(status='failed', current_step='content_audit', error=str(exc))
        save(job_id, 'editorial_recovery', state)
        raise EditorialRecoveryError('修正後の全文QAが現在の原稿へ対応していません。') from exc
    clear_pending_marker(job_id)
    state.update(status='completed', final_snapshot=report['snapshot'])
    save(job_id, 'editorial_recovery', state)
    return saved


def _run(job_id, keyword, api_key=None):
    from . import step_content_audit
    from .content_quality import _require_audit_strict, snapshot
    previous = get_optional_artifact(job_id, 'editorial_recovery')
    if previous:
        state = json.loads(previous['content_text'])
        if state.get('status') != 'completed':
            return continue_recovery(job_id, keyword, state, api_key)
        context = audit_context(job_id, keyword)
        if state.get('final_snapshot') == snapshot(**context):
            saved = get_artifact(job_id, 'content_audit')
            _require_audit_strict(json.loads(saved['content_text']), state['final_snapshot'], stage='article', context=context)
            clear_pending_marker(job_id)
            return saved
    try:
        result = step_content_audit.run(job_id, keyword, api_key=api_key)
        if previous:
            # A completed recovery can need a new audit after a policy/body
            # change. Advance its verification receipt only after current,
            # strict whole-article QA; never replay or relabel writing steps.
            context = audit_context(job_id, keyword)
            fresh = json.loads(get_artifact(job_id, 'content_audit')['content_text'])
            _require_audit_strict(fresh, snapshot(**context), stage='article', context=context)
            state.setdefault('verification_history', []).append(state['final_snapshot'])
            state.update(final_snapshot=fresh['snapshot'], expected_hashes=current_hashes(job_id))
            clear_pending_marker(job_id)
            save(job_id, 'editorial_recovery', state)
        return result
    except UpstreamRepairRequired as exc:
        if previous:
            raise EditorialRecoveryError('上流へ戻す修正は既に1回実行しました。追加生成せず指摘を保持します。') from exc
        state = begin(job_id, keyword, exc.report)
        return continue_recovery(job_id, keyword, state, api_key)


def run(job_id, keyword, api_key=None):
    # Do not run two recovery controllers for the same local worker/job. Across
    # hosts, the existing revision RPC remains the concurrency boundary.
    folder = Path(os.getenv('QUALITY_BUDGET_DIR') or tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (digest(job_id) + '.editorial-recovery.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EditorialRecoveryError('同じ記事の品質確認・上流修正が実行中です。重複送信しません。') from exc
        return _run(job_id, keyword, api_key)
