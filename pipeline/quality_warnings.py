"""Per-stage warning policy. Invalid audits remain invalid in saved evidence."""
import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps

_CONTEXT = ContextVar('quality_warning_context', default=None)
WARNING_ACCOUNT = 'nishi.aworks@gmail.com'
SOFT_STAGES = {'research_completeness', 'structure_guard', 'research_validation',
               'review', 'fact_review', 'content_audit', 'final_structure_validation'}

def enabled():
    return _CONTEXT.get() is not None

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
                from .autofix import classify_error
                # Provider billing/authentication errors must not be concealed.
                if re.search(r'残高|insufficient_quota|billing|credit balance|API.?key|authentication|unauthorized|timeout|timed out|connection|429|502|503', str(exc), re.I):
                    raise
                if stage not in SOFT_STAGES or classify_error(exc)['reason'] != '記事の根拠・比較内容が品質基準を満たしていません':
                    raise
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
