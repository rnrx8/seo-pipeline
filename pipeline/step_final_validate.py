"""Record publication readiness separately from delivering the saved draft."""
from __future__ import annotations

import json
from .step_cta_inject import cta_placement_issues

from .db import get_artifact, get_optional_artifact, get_job, upsert_artifact
from .article_quality import validate_delivery
from .readability import readability_issues
from .claim_scope import scope_issues
from .content_quality import ContentQualityError, audit_facts, final_review_requirements, snapshot, source_evidence
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
    if job.get('citation_style') == 'inline_footnote':
        from .article_citations import integrity_issues
        violations += integrity_issues(article['content_text'],(article.get('meta') or {}).get('citation_registry'))
    recovery_artifact = get_optional_artifact(job_id, 'editorial_recovery')
    recovery = None
    if recovery_artifact:
        try:
            recovery = json.loads(recovery_artifact['content_text'])
            if not isinstance(recovery, dict) or recovery.get('status') != 'completed':
                raise ValueError('not completed')
        except (ValueError, TypeError):
            violations.append({'key': 'editorial_recovery_pending', 'reason': '上流修正と全文QAが未完了です。'})
    violations += validate_delivery(article['content_text'], outline, job.get('word_count_setting'), contract=contract, section_map=(article.get('meta') or {}).get('section_map'))
    research_status=None
    try:
        from .research_requirements import require_authoring_matrix
        matrix=require_authoring_matrix(job_id)
        # Research-list completeness remains visible, but article acceptance is
        # determined below by current full evidence/coverage/reader QA. An
        # unused unknown sibling does not revoke supported published claims.
        research_status={'all_questions_complete':matrix.get('valid') is True,
                         'acceptance_basis':'current_material_and_full_article_audit'}
    except Exception as exc:
        violations.append({'key':'research_not_ready','reason':str(exc)[:200]})
    try:
        facts = audit_facts(get_artifact(job_id, 'fact_sheet')['content_text'], article,
                           high_accuracy=bool(job.get('high_accuracy_mode')),
                           evidence=get_artifact(job_id, 'fact_review_evidence') if job.get('high_accuracy_mode') else None)
        violations += scope_issues(article['content_text'], facts)
        source_artifacts = [get_artifact(job_id, 'fresh_sources')]
        if job.get('high_accuracy_mode'): source_artifacts.append(get_artifact(job_id, 'fresh_sources_review'))
        sources = source_evidence(*source_artifacts)
        audit_report = json.loads(get_artifact(job_id, 'content_audit')['content_text'])
        context = dict(text=article['content_text'], facts=facts, outline=outline, contract=contract,
                       requirements=final_review_requirements(job, keyword), sources=sources)
        if recovery_artifact and (not isinstance(recovery, dict) or recovery.get('final_snapshot') != snapshot(**context)):
            violations.append({'key': 'editorial_recovery_not_current', 'reason': '上流修正の最終確認が現在の原稿へ対応していません。'})
        from .content_quality import _require_audit_strict
        _require_audit_strict(audit_report, snapshot(**context), stage="article", context=context)
    except Exception as exc:
        violations.append({'key': 'content_audit_not_passed', 'reason': str(exc)[:200]})
    report = {
        "stage": "final_article",
        "valid": not violations,
        "violations": violations,
        "research_status":research_status,
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
        from .quality_warnings import enabled, warn
        if enabled():
            # Completion is not publication approval. Keep all failed checks,
            # recovery state and cost receipts; never seal an unverified body.
            warn('記事は生成済みです。公開前の確認事項: '+json.dumps(violations,ensure_ascii=False))
            upsert_artifact(job_id=job_id,step='article',content_type=article.get('content_type','text/markdown'),
                content_text=article['content_text'],meta={**(article.get('meta') or {}),
                    'content_audited':False,'content_audit_snapshot':None,
                    'publication_approved':False,'completion_with_warnings':True})
            return artifact
        if recovery_artifact:
            from .editorial_recovery import EditorialRecoveryError
            raise EditorialRecoveryError(f'Final recovered article violates content contract: {violations}')
        raise ContentQualityError(f"Final article violates content contract: {violations}")
    from .article_revisions import seal
    if (article.get('meta') or {}).get('completion_with_warnings'):
        upsert_artifact(job_id=job_id,step='article',content_type=article.get('content_type','text/markdown'),
            content_text=article['content_text'],meta={**article['meta'],'completion_with_warnings':False})
    seal(job_id)
    from .quality_warnings import resolve_after_final
    warnings = get_optional_artifact(job_id,'quality_warnings')
    if warnings:resolve_after_final(job_id)
    print("[final_validate] Final article satisfies the content contract")
    return artifact
