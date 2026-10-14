"""Optional rollout of immutable article versions and atomic generation checkpoints."""
from contextvars import ContextVar
from contextlib import contextmanager
import os
import requests

RUN = ContextVar('article_revision_run', default=None)
BOUND_STEPS = {'serp','serp_verified','query_attrs','search_intent','intent_chains','reference_structure','research_plan',
               'fact_sheet','fresh_sources','fresh_sources_review','research_matrix','content_contract',
               'outline','service_map','article','fact_review_evidence','content_audit','structure_validation_final'}


def enabled():
    return os.getenv('ARTICLE_REVISION_TRACKING', '0') == '1'


def rpc(name, payload):
    from .db import _base, _headers
    response = requests.post(_base() + '/rpc/' + name, headers=_headers(), json=payload, timeout=30)
    if response.status_code == 409:
        from .content_quality import ContentQualityError
        raise ContentQualityError('制作中に本文・構成・設定が変更されました。現在の版を確認してから再開してください。')
    response.raise_for_status()
    return response.json() if response.content else None


@contextmanager
def generation(job_id):
    if not enabled():
        yield
        return
    try:
        saved = rpc('begin_article_run', {'p_job_id': job_id})
        if not isinstance(saved, dict) or not saved.get('id') or not isinstance(saved.get('inherited_revisions'), dict):
            raise ValueError('Missing generation revision receipt')
    except Exception as exc:
        # This happens before the runner's own error handler. Do not leave a
        # claimed job running or start paid stages without its revision receipt.
        from .db import update_job_status, update_job_error
        try:
            update_job_status(job_id, 'failed')
            update_job_error(job_id, '制作版の開始記録を保存できません: ' + type(exc).__name__)
        except Exception as record_error:
            print('[revisions] Could not record start failure:', type(record_error).__name__)
        raise
    token = RUN.set({'job_id': job_id, 'id': saved['id'], 'revisions': dict(saved['inherited_revisions'])})
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        try:
            if not RUN.get().get('sealed'):
                try:
                    rpc('finish_article_run', {'p_run_id': saved['id']})
                except Exception as exc:
                    if not failed:
                        raise
                    # Keep the primary generation error; the unfinished run
                    # cannot be sealed and remains visible for investigation.
                    print('[revisions] Could not finish failed run:', type(exc).__name__)
        finally:
            RUN.reset(token)


def observe(job_id, step, artifact):
    current = RUN.get()
    if not current or current['job_id'] != job_id or step not in BOUND_STEPS:
        return artifact
    revision = artifact.get('revision_id') if artifact else None
    expected = current['revisions'].get(step)
    if expected != revision:
        from .content_quality import ContentQualityError
        raise ContentQualityError('参照資料の版が制作開始後に変わりました。古い確認結果を現在の版へ流用しません。')
    return artifact


def save(job_id, step, data):
    current = RUN.get()
    if not current or current['job_id'] != job_id:
        return None
    expected = current['revisions'].get(step)
    if step not in BOUND_STEPS:
        from .db import get_optional_artifact
        previous = get_optional_artifact(job_id, step)
        expected = previous.get('revision_id') if previous else None
    saved = rpc('save_run_artifact', {'p_run_id': current['id'], 'p_step': step,
                                     'p_data': data, 'p_expected_revision': expected})
    if not isinstance(saved, dict) or not saved.get('revision_id'):
        from .content_quality import ContentQualityError
        raise ContentQualityError('成果物の保存版を確認できません。旧保存経路へ再送しません。')
    if step in BOUND_STEPS:
        current['revisions'][step] = saved['revision_id']
    return saved


def generated_length(job_id, setting):
    current = RUN.get()
    if not current or current['job_id'] != job_id:
        return False
    rpc('set_run_generated_length', {'p_run_id': current['id'], 'p_value': setting})
    return True


def seal(job_id):
    current = RUN.get()
    if not current or current['job_id'] != job_id:
        return
    result = rpc('seal_article_completion', {'p_job_id': job_id, 'p_run_id': current['id'],
                                         'p_expected_revisions': current['revisions']})
    current['sealed'] = True
    return result


def revision_run(fn):
    from functools import wraps
    import inspect
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        bound = inspect.signature(fn).bind(*args, **kwargs)
        job_id = bound.arguments.get('job_id')
        if job_id is None:
            from .db import insert_job
            job_id = insert_job(bound.arguments['keyword'])
            bound.arguments['job_id'] = job_id
        with generation(job_id):
            return fn(*bound.args, **bound.kwargs)
    return wrapped
