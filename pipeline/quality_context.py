"""Route research records only to the editorial roles that use them."""
import copy
import json

CONTEXT_POLICY = 'role-input-v3-scoped-resolutions'


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
                    if k not in ('quote','expert_qualification_quote') and v not in ('',None)} for r in refs]
            for key in ('official_checked_urls','exploration_reason','omission_source_review','omission_candidate_from','unresolved_candidate','reviewed_source_urls','additional_sources_needed','exploration_complete'):
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


def canonical_answers_match(facts, requirements):
    from .content_quality import digest
    decisions=requirements.get('research_decisions')
    return (isinstance(decisions,dict) and decisions.get('valid') is True
            and bool(decisions.get('items')) and decisions.get('facts_sha256')==digest(facts))


def review_conditions(facts, requirements):
    """Canonical answers already include the literal conditions; don't copy them again."""
    if compact_enabled() and canonical_answers_match(facts, requirements):
        return '対象・プラン・期間・無料範囲等の条件は requirements.research_decisions の各回答と一体で照合する。'
    from .claim_scope import conditional_facts
    return conditional_facts(facts)


def review_facts(facts, requirements):
    """Keep one authoritative copy of canonical answers and omit quote duplication."""
    if not compact_enabled():return facts
    if canonical_answers_match(facts, requirements):
        return '確認済み回答・条件・出典と省略状態は requirements.research_decisions に一度だけ掲載。omitted の内容を本文で断定しない。'
    # Noncanonical or additional fact-review evidence must never disappear.
    # Remove only parseable source-quotation annotations, retaining their URL.
    import re
    lines=[]
    for line in facts.splitlines():
        match=re.fullmatch(r'(出典：https?://[^\s｜]+)｜確認箇所：(.*)',line)
        if match:
            try:
                if isinstance(json.loads(match[2]),str):line=match[1]
            except ValueError:pass
        lines.append(line)
    return '\n'.join(lines)
