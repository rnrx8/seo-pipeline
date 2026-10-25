"""One deterministic writing prerequisite; never a publication-quality verdict."""
import json

from .content_quality import ContentQualityError, digest
from .db import get_artifact, get_job, upsert_artifact

VERSION = 'draft-readiness-v1'


class DraftReadinessError(ContentQualityError):
    """Missing/stale writing inputs must not turn into a warning continuation."""


def _evaluate(job_id, keyword, *, outline=None, load=None, job=None):
    from .research_requirements import load_plan, require_matrix
    from .quality_warnings import enabled, warn
    from .editorial_blueprint import context, require_projection, budget_issues, fingerprint
    from .outline_policy import require_current_outline
    from .outline_design import assignment_issues
    from .step_structure_guard import validate_structure
    fetch=load or get_artifact
    def load(job_id, step):
        artifact=fetch(job_id,step)
        if not isinstance(artifact,dict) or not isinstance(artifact.get('content_text'),str):
            raise DraftReadinessError('下書きの入力資料がありません: '+step)
        return artifact
    job=job if job is not None else get_job(job_id)
    # There is one current-input rule for guard, writer, service map and resume.
    # Do not import old model pass/fail reports into this mechanical decision.
    # Research QA is advisory in the generation flow. require_matrix still
    # filters stale/unverified answers out of the writer's evidence bundle.
    matrix=require_matrix(job_id)
    plan=load_plan(job_id)
    saved=load(job_id,'outline')
    if outline is not None and saved!=outline:
        # DB IDs/timestamps may differ in test/staged callers. The actual
        # rendered design and its metadata must be the same current revision.
        if (saved['content_text'],saved.get('meta',{}))!=(outline['content_text'],outline.get('meta',{})):
            raise DraftReadinessError('本文開始前に構成の版が変わりました。現在の構成を確認してください。')
    outline=saved
    require_current_outline(outline)
    value=require_projection(outline,context(plan,matrix))
    if value is None:raise DraftReadinessError('構造化された編集設計がありません。')
    contract=json.loads(load(job_id,'content_contract')['content_text'])
    issues=assignment_issues(outline['content_text'],plan)+validate_structure(outline['content_text'],contract,outline=True)
    issues+=budget_issues(value,job.get('word_count_setting'))
    if issues:
        message='下書きに必要な構成・回答の割当てに確認事項があります: '+json.dumps(issues,ensure_ascii=False)
        if not enabled():raise DraftReadinessError(message)
        warn(message)
    settings={k:job.get(k) for k in ('custom_prompt','must_include','must_reference_urls','never_reference_urls',
        'company_restriction','word_count_setting','article_purpose','target_audience','tone_style','citation_style','service_id','cta_id',
        'tenant_id','category','high_accuracy_mode','delivery_type')}
    inputs={'plan':plan,'matrix':matrix,'outline':outline['content_text'],
            'blueprint':value,'contract':contract,'keyword':keyword,'settings':settings}
    for step in ('fact_sheet','fresh_sources'):
        inputs[step]=load(job_id,step)['content_text']
    binding=digest(json.dumps(inputs,ensure_ascii=False,sort_keys=True))
    return {'version':VERSION,'kind':'draft_readiness','ready_for_draft':True,'publication_approved':False,
            'input_sha256':binding,'checks':{'research_current':matrix.get('valid') is True,
            'canonical_design_current':outline.get('meta',{}).get('blueprint_inputs_sha256')==fingerprint(context(plan,matrix)),
            'required_answers_assigned':not issues,'structure_and_explicit_budget':not issues},
            'warnings':issues,
            'editorial_quality':'verify_on_actual_article'}


def evaluate(job_id, keyword, **kwargs):
    try:return _evaluate(job_id,keyword,**kwargs)
    except ContentQualityError as exc:
        if isinstance(exc,DraftReadinessError):raise
        raise DraftReadinessError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise DraftReadinessError('下書きの入力資料のJSONが不正です。資料を保持して確認してください。') from exc
    except ValueError as exc:
        if str(exc).startswith('Artifact not found:'):
            raise DraftReadinessError(str(exc)) from exc
        raise


def run(job_id, keyword, api_key=None, *, load=None, job=None, save=None):
    del api_key
    report=evaluate(job_id,keyword,load=load,job=job)
    return (save or upsert_artifact)(job_id=job_id,step='draft_readiness',content_type='application/json',
        content_text=json.dumps(report,ensure_ascii=False),meta={'ready_for_draft':True,'publication_approved':False,'model_calls':0})
