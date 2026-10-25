"""Single source of truth for API and CLI pipeline execution order."""
from __future__ import annotations

from collections.abc import Callable
import json
from .ai import astra_review_enabled, tiered_review_enabled
from . import research_requirements

from . import (
    step_article,
    step_content_contract,
    step_cta_inject,
    step_fact_review,
    step_fact_sheet,
    step_final_validate,
    step_intent,
    step_outline,
    step_reference,
    step_review,
    step_serp,
    step_service_map,
    step_structure_guard,
    step_research_guard,
    step_content_audit,
)

Step = tuple[str, Callable]
RATE_LIMITED_STEPS = {
    "search_intent", "fact_sheet", "outline", "service_map", "article", "review", "fact_review",
    "content_audit", "research_plan", "research_completeness"
}


def requires_rate_limit_delay(step_key: str) -> bool:
    return step_key in RATE_LIMITED_STEPS


def research_resume_stage(job_id, stage, keys, load):
    if stage=='research_plan' and 'fact_sheet' in keys:
        saved=load(job_id,'research_plan')
        if (saved or {}).get('meta',{}).get('ready_for_research') is True:
            # A paid plan already exists; an optional preflight error must not
            # send the entire plan writer again on resume.
            research_requirements.load_plan(job_id)
            return 'fact_sheet'
    stage = _research_resume_stage(job_id, stage, keys, load)
    # Old warn-through runs can fail at final validation even though the actual
    # unfinished prerequisite is article QA. Resume that saved draft's audit,
    # never the writer or a final check that cannot repair its prerequisite.
    audit = 'content_audit'
    if audit in keys and stage in keys and keys.index(stage) > keys.index(audit):
        saved = load(job_id, audit)
        try:
            report = json.loads(saved['content_text']) if saved else {}
        except (ValueError, TypeError):
            report = {}
        if not isinstance(report, dict) or report.get('valid') is not True:
            return audit
    return stage


def _research_resume_stage(job_id, stage, keys, load):
    """Repair structured research prerequisites before resuming a later stage.

    The location of a failure does not prove its research gate passed. Reuse
    saved research and request receipts, never rerun discovery from the start.
    Transport/authentication errors propagate instead of enabling new work.
    """
    prerequisite = 'research_completeness'
    if prerequisite not in keys or stage not in keys or keys.index(stage) <= keys.index(prerequisite):
        return stage
    intent = load(job_id, 'search_intent')
    if not ((intent or {}).get('meta') or {}).get('reader_needs_version'):
        return stage
    matrix = load(job_id, 'research_matrix')
    if not matrix:return prerequisite
    from .content_quality import ContentQualityError
    try:
        if not research_requirements.matrix_ready_for_draft(job_id,json.loads(matrix['content_text'])):
            return prerequisite
    except ContentQualityError:
        return prerequisite
    outline_gate='research_validation'
    if outline_gate in keys and keys.index(stage)>keys.index(outline_gate):
        outline=load(job_id,'outline')
        if (outline or {}).get('meta',{}).get('blueprint_version'):
            readiness=load(job_id,'draft_readiness')
            try:report=json.loads(readiness['content_text']) if readiness else {}
            except json.JSONDecodeError:return outline_gate
            from .draft_readiness import evaluate,VERSION
            if not isinstance(report,dict) or report.get('version')!=VERSION or report.get('ready_for_draft') is not True:return outline_gate
            try:
                from .db import get_job
                job=get_job(job_id)
                current=evaluate(job_id,job['main_keyword'],outline=outline,load=load,job=job)
                if current['input_sha256']!=report.get('input_sha256'):return outline_gate
            except ContentQualityError:return outline_gate
    return stage


def build_step_plan(job: dict) -> list[Step]:
    """Return the exact step plan for one job.

    Both production API execution and local/CLI execution must use this function
    so service placement and structural validation cannot silently diverge.
    """
    delivery_type = job.get("delivery_type") or "full"
    steps: list[Step] = [
        ("serp", step_serp.run),
        ("search_intent", step_intent.run),
        ("research_plan", research_requirements.plan),
        ("fact_sheet", step_fact_sheet.run),
        ("research_completeness", research_requirements.verify),
    ]
    if delivery_type == "research_only":
        return budgeted_steps(steps)

    steps.extend([
        ("reference_structure", step_reference.run),
        ("content_contract", step_content_contract.run),
        ("outline", step_outline.run),
        # Repair required comparison/service sections before service_map chooses
        # its primary section and CTA positions.
        ("structure_guard", step_structure_guard.run_before_research),
        ("research_validation", step_research_guard.run),
    ])
    if job.get("service_id") or job.get("cta_id"):
        steps.append(("service_map", step_service_map.run))
    if delivery_type == "outline_only":
        return budgeted_steps(steps)

    steps.append(("article", step_article.run))
    if job.get("cta_id"):
        steps.append(("cta_inject", step_cta_inject.run))
    if not astra_review_enabled():
        steps.append(("review", step_review.run))
    if job.get("high_accuracy_mode"):
        steps.append(("fact_review", step_fact_review.run))
    from .editorial_recovery import run as audit_with_recovery
    steps.append(("content_audit", audit_with_recovery))
    steps.append(("final_structure_validation", step_final_validate.run))
    return budgeted_steps(steps)


def budgeted_steps(steps):
    from .quality_warnings import scoped as warning_scoped
    if tiered_review_enabled():
        from .quality_budget import scoped
        steps = [(key, scoped(fn, stage=key)) for key, fn in steps]
    return [(key, warning_scoped(fn, key)) for key, fn in steps]
