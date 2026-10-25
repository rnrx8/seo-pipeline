"""Per-stage warning policy. Invalid audits remain invalid in saved evidence."""
import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from contextlib import contextmanager

_CONTEXT = ContextVar('quality_warning_context', default=None)
WARNING_ACCOUNT = 'nishi.aworks@gmail.com'
SOFT_STAGES = {'research_completeness', 'structure_guard', 'research_validation',
               'review', 'fact_review', 'content_audit'}


def resolve_after_final(job_id):
    """Keep history, but do not present resolved earlier attempts as current faults.

    Called only after native final validation and save/seal. Re-read its strict
    result so this helper cannot clear warnings from a failed/incomplete run.
    """
    from .db import get_optional_artifact, get_artifact, upsert_artifact
    from .content_quality import ContentQualityError
    final = json.loads(get_artifact(job_id, 'structure_validation_final')['content_text'])
    if final.get('valid') is not True or final.get('violations'):
        raise ContentQualityError('最終確認が未合格の警告は解消扱いにできません。')
    previous = get_optional_artifact(job_id, 'quality_warnings')
    if not previous:return
    value=json.loads(previous['content_text'])
    warnings=value.get('warnings',[])
    if not warnings:return
    value.setdefault('resolved_warnings',[]).extend(
        [{**w,'resolved_at':datetime.now(timezone.utc).isoformat(),'resolution':'native_final_validation_passed'} for w in warnings])
    value['warnings']=[]
    upsert_artifact(job_id=job_id,step='quality_warnings',content_type='application/json',
                    content_text=json.dumps(value,ensure_ascii=False))

def enabled():
    return _CONTEXT.get() is not None


@contextmanager
def strict_checks():
    """A recovery must pass its prerequisites before it can write new prose."""
    token = _CONTEXT.set(None)
    try:
        yield
    finally:
        _CONTEXT.reset(token)

def warn(message):
    context = _CONTEXT.get()
    if context is None:
        from .content_quality import ContentQualityError
        raise ContentQualityError(message)
    from .db import get_optional_artifact, upsert_artifact, get_job, get_user_email
    job_id, stage = context
    previous = get_optional_artifact(job_id, 'quality_warnings')
    value = json.loads(previous['content_text']) if previous else {'policy': 'warn_and_continue', 'warnings': []}
    if any(w['stage'] == stage and w['message'] == str(message) for w in value['warnings']):
        return
    warning = {'stage': stage, 'message': str(message), 'at': datetime.now(timezone.utc).isoformat()}
    value['warnings'].append(warning)
    # Persist before continuing; inability to retain the warning is a real failure.
    upsert_artifact(job_id=job_id, step='quality_warnings', content_type='application/json',
                    content_text=json.dumps(value, ensure_ascii=False))
    email = get_user_email(get_job(job_id).get('tenant_id', ''))
    if email.lower() != WARNING_ACCOUNT:
        from .notify import alert_quality_warning
        alert_quality_warning(job_id, email, stage, str(message))

def scoped(fn, stage):
    @wraps(fn)
    def run(job_id, *args, **kwargs):
        token = _CONTEXT.set((job_id, stage))
        try:
            from .content_quality import ContentQualityError
            try:
                result = fn(job_id, *args, **kwargs)
            except ContentQualityError as exc:
                from .editorial_recovery import EditorialRecoveryError, UpstreamRepairRequired
                from .draft_readiness import DraftReadinessError
                if isinstance(exc, (EditorialRecoveryError, UpstreamRepairRequired, DraftReadinessError)):
                    raise
                # Editorial diagnostics may continue to actual article QA.
                # An unavailable provider must preserve its cause/current
                # stage, rather than being masked by the next prerequisite.
                if stage not in SOFT_STAGES:
                    raise
                provider_unavailable = bool(re.search(
                    r'残高|利用枠|insufficient_quota|billing|credit balance|API.?key|authentication|unauthorized|response\.incomplete|応答が未完了|timeout|timed out|connection|429|502|503', str(exc), re.I))
                if provider_unavailable:raise
                warn(str(exc))
                result = None
            if stage == 'structure_guard':
                from .db import get_optional_artifact
                report = get_optional_artifact(job_id, 'structure_validation')
                if report:
                    value = json.loads(report['content_text'])
                    if value.get('valid') is False:
                        warn(value.get('error') or '構成の確認事項が残っています。')
            return result
        finally:
            _CONTEXT.reset(token)
    return run
