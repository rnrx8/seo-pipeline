"""Fail a job when its final article violates protected structural requirements."""
from __future__ import annotations

import json
from .step_cta_inject import cta_placement_issues

from .db import get_artifact, get_job, upsert_artifact
from .article_quality import validate_delivery
from .readability import readability_issues
from .claim_scope import scope_issues
from .content_quality import ContentQualityError, audit_facts, require_audit, final_review_requirements, snapshot, source_evidence
from .step_structure_guard import extract_h2_titles, validate_structure


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    del api_key
    article = get_artifact(job_id, "article")
    contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
    violations = readability_issues(article["content_text"])
    violations += validate_structure(article["content_text"], contract, outline=False)
    violations += cta_placement_issues(article['content_text'], (article.get('meta') or {}).get('cta_placement'), (article.get('meta') or {}).get('section_map'))
    outline = get_artifact(job_id, 'outline')['content_text']
    job = get_job(job_id)
    violations += validate_delivery(article['content_text'], outline, job.get('word_count_setting'), contract=contract, section_map=(article.get('meta') or {}).get('section_map'))
    try:
        facts = audit_facts(get_artifact(job_id, 'fact_sheet')['content_text'], article,
                           high_accuracy=bool(job.get('high_accuracy_mode')),
                           evidence=get_artifact(job_id, 'fact_review_evidence') if job.get('high_accuracy_mode') else None)
        violations += scope_issues(article['content_text'], facts)
        source_artifacts = [get_artifact(job_id, 'fresh_sources')]
        if job.get('high_accuracy_mode'): source_artifacts.append(get_artifact(job_id, 'fresh_sources_review'))
        sources = source_evidence(*source_artifacts)
        audit_report = json.loads(get_artifact(job_id, 'content_audit')['content_text'])
        require_audit(audit_report, snapshot(article['content_text'], facts, outline, contract,
                                             final_review_requirements(job, keyword), sources), stage="article")
    except Exception as exc:
        violations.append({'key': 'content_audit_not_passed', 'reason': str(exc)[:200]})
    report = {
        "stage": "final_article",
        "valid": not violations,
        "violations": violations,
        "h2s": extract_h2_titles(article["content_text"], outline=False),
    }
    artifact = upsert_artifact(
        job_id=job_id,
        step="structure_validation_final",
        content_type="application/json",
        content_text=json.dumps(report, ensure_ascii=False),
        meta={"stage": "final_article", "valid": not violations},
    )
    if violations:
        raise ContentQualityError(f"Final article violates content contract: {violations}")
    print("[final_validate] Final article satisfies the content contract")
    return artifact
