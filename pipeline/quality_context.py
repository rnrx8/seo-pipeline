"""Route research records only to the editorial roles that use them."""
import copy
import json

CONTEXT_POLICY = 'role-input-v1-brief-decisions'


def compact_enabled():
    from .ai import tiered_review_enabled
    from .focused_research import enabled
    return tiered_review_enabled() and enabled()


def decision_brief(requirements):
    """Project records, never summarize answers/conditions or edit stored facts."""
    result=copy.deepcopy(requirements)
    decisions=result.get('research_decisions')
    if isinstance(decisions,dict):
        for item in decisions.get('items',[]):
            # Source text belongs to the evidence reviewer. All answer, date,
            # adoption/omission and unknown future fields survive verbatim.
            refs=item.pop('evidence',[])
            if refs:
                item['evidence_sources']=[{k:v for k,v in r.items()
                    if k not in ('quote','expert_qualification_quote')} for r in refs]
            for key in ('official_checked_urls','exploration_reason','omission_source_review','omission_candidate_from'):
                item.pop(key,None)
    return result


def review_requirements(requirements, role):
    # All user/editorial settings survive unchanged. Research evidence is not
    # needed to judge prose, internal consistency, or information presentation.
    result=copy.deepcopy(requirements)
    if role not in ('evidence','coverage','research'):
        result.pop('research_plan',None)
        result.pop('research_decisions',None)
    else:
        plan=result.get('research_plan')
        if isinstance(plan,dict):
            result['research_plan']={k:copy.deepcopy(v) for k,v in plan.items()
                                     if k not in ('policy_sha256','plan_retirements')}
        decisions=result.get('research_decisions')
        if isinstance(decisions,dict):
            # Preserve every decision, quotation, limitation and coverage gap.
            # Only runtime bookkeeping is removed, never fact/evidence fields.
            result['research_decisions']={k:copy.deepcopy(v) for k,v in decisions.items()
                                         if k not in ('policy_sha256','attempt','plan_sha256',
                                                      'sources_sha256','attempts_sha256','facts_sha256')}
    return decision_brief(result) if compact_enabled() and role in ('evidence','coverage','research') else result


def repair_context(requirements, sources, failed_checks, structural_issues, report=None):
    """Pure style fixes need the article and fact conditions, not source bodies."""
    if not compact_enabled():return requirements,sources
    style={'prose_quality','redundancy'}
    structural={'continuous_prose','duplicate_prose'}
    only_style=(bool(failed_checks or structural_issues)
                and all(c.get('key') in style for c in failed_checks)
                and all(c.get('key') in structural for c in structural_issues))
    if only_style:return review_requirements(requirements,'language'),''
    # Narrow factual repairs only to an independently reviewed, version-bound
    # source set covering every failed block. Other repairs retain all bodies.
    from .focused_quality import ROLES
    from .content_quality import digest
    if failed_checks and not structural_issues and all(c.get('key') in ROLES['evidence'] for c in failed_checks):
        trace=(((report or {}).get('phases') or {}).get('evidence') or {}).get('evidence_routing') or []
        if trace and trace[-1].get('model')=='gpt-6.1-sol' and trace[-1].get('review',{}).get('needed') is False:
            last=trace[-1]
            affected={l['id'] for c in failed_checks for l in c.get('affected_blocks',[])}
            try:
                pages=json.loads(sources)
                selected=[p for p in pages if p['url'] in last.get('visible_source_urls',[])]
                complete_locations=all(c.get('affected_blocks') for c in failed_checks)
                if (complete_locations and affected<=set(last.get('reviewed_block_ids',[])) and selected
                    and digest(json.dumps(selected,ensure_ascii=False,sort_keys=True))==last.get('visible_sources_sha256')):
                    sources=json.dumps(selected,ensure_ascii=False)
            except (ValueError,TypeError,KeyError):
                pass  # A malformed or changed receipt never permits narrowing.
    return review_requirements(requirements,'evidence'),sources
