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
    "research_validation", "content_audit", "research_plan", "research_completeness"
}


def requires_rate_limit_delay(step_key: str) -> bool:
    return step_key in RATE_LIMITED_STEPS


def research_resume_stage(job_id, stage, keys, load):
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
    if not matrix or json.loads(matrix['content_text']).get('valid') is not True:
        return prerequisite
    from .content_quality import ContentQualityError
    try:
        research_requirements._require_matrix_strict(job_id)
    except ContentQualityError:
        return prerequisite
    outline_gate='research_validation'
    if outline_gate in keys and keys.index(stage)>keys.index(outline_gate):
        outline=load(job_id,'outline')
        if (outline or {}).get('meta',{}).get('blueprint_version'):
            readiness=load(job_id,'research_validation')
            report=json.loads(readiness['content_text']) if readiness else {}
            if report.get('valid') is not True:return outline_gate
            from .editorial_blueprint import require_review
            try:require_review(report,outline)
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
