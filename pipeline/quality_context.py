"""Route research records only to the editorial roles that use them."""
import copy
import json

CONTEXT_POLICY = 'role-input-v7-canonical-claim-projection'


def compact_enabled():
    from .ai import tiered_review_enabled
    from .focused_research import enabled
    return tiered_review_enabled() and enabled()


def decision_brief(requirements):
    """Project records, never summarize answers/conditions or edit stored facts."""
    result=copy.deepcopy(requirements)
    decisions=result.get('research_decisions')
    if isinstance(decisions,dict):
        from .claim_evidence import VERSION as CLAIM_VERSION
        canonical=bool(decisions.get('items')) and all(
            item.get('claim_format')==CLAIM_VERSION and isinstance(item.get('claim_states'),list)
            for item in decisions['items'])
        if canonical:
            # Keep the original matrix in storage. A reviewer needs one copy
            # of each verified claim, not candidates plus repeated audit logs.
            decisions.pop('evidence_ledger',None)
            for key in ('answer_repair_history','answer_repair_version','answer_repair_rounds'):
                decisions.pop(key,None)
        for item in decisions.get('items',[]):
            if canonical:
                item['claims']=item.pop('claim_states')
                for claim in item['claims']:
                    for field in ('evidence','conflicting_evidence'):
                        for ref in claim.get(field,[]):
                            # Source documents remain available to evidence QA;
                            # preserve every URL, condition and disposition.
                            ref.pop('quote',None);ref.pop('expert_qualification_quote',None)
                for key in ('claim_evidence','claims_checked_at','claim_validation_issue','claim_visibility_failed','claim_policy_issues'):
                    item.pop(key,None)
            # Source text belongs to the evidence reviewer. All answer, date,
            # adoption/omission and unknown future fields survive verbatim.
            refs=item.pop('evidence',[])
            if refs:
                item['evidence_sources']=[{k:v for k,v in r.items()
                    if k not in ('quote','expert_qualification_quote') and v not in ('',None)} for r in refs]
            for key in ('official_checked_urls','exploration_reason','omission_source_review','omission_candidate_from','unresolved_candidate','reviewed_source_urls','additional_sources_needed','exploration_complete'):
                item.pop(key,None)
    return result


# Route the existing built-in checklist, not user rules. Unknown future sections
# are retained so extending the checklist cannot silently discard requirements.
EDITORIAL_OWNERS = {
    '### 【文章の意味と自然さ】': ('language', 'coverage', 'consistency'),
    '1. 共感表現の語尾': ('language',),
    '2. H2直下の結論文チェック': ('coverage', 'organization'),
    '3. H3直下の本文チェック': ('coverage', 'organization'),
    '4. 表・リスト直前の導入文チェック': ('organization',),
    '5. プレーンテキスト連続300字超の検出・修正': ('organization',),
    '6. 長文チェック': ('language',),
    '7. 文体チェック': ('language',),
    '8. H2見出しの可読性チェック': ('organization',),
    '9. 紹介サービス見出しの重複チェック': ('consistency',),
    '10. 学習済み執筆ルールの適用': ('language',),
    '11. 並列・列挙内容のリスト化チェック': ('organization',),
}


def editorial_rules_for_role(rules, role):
    from .step_review import SYSTEM_PROMPT
    import re
    builtin = SYSTEM_PROMPT.split('【チェック・修正項目】', 1)[1].split('【出力フォーマット】', 1)[0]
    if rules != builtin or role not in ('evidence', 'coverage', 'language', 'consistency', 'organization'):
        return rules
    sections = re.split(r'(?m)(?=^### 【|^\d+\. )', rules)
    selected = []
    for section in sections:
        owners = next((owners for prefix, owners in EDITORIAL_OWNERS.items()
                       if section.startswith(prefix)), None)
        if owners is None or role in owners:
            selected.append(section)
    return ''.join(selected)


def review_requirements(requirements, role):
    # All user/editorial settings survive unchanged. Research evidence is not
    # needed to judge prose, internal consistency, or information presentation.
    result=copy.deepcopy(requirements)
    if role!='coverage':result.pop('article_design',None)
    # Outline readiness and article coverage share deeper search intent.
    # Other reviewers retain the shared policy but do not reread the analysis.
    if role not in ('coverage', 'research'):
        result.pop('intent_value_context', None)
        result.pop('intent_analysis', None)
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


def review_reference_date(stage, fingerprint=None):
    """Resume the same frozen review as of its original date, not today's date.

    A changed snapshot starts a new review date. This does not assert that old
    sources have been refreshed; callers must supply the full snapshot identity.
    """
    from datetime import datetime, timezone, date
    from .quality_budget import JOB
    from .tiered_research import get_optional_artifact, upsert_artifact
    today = datetime.now(timezone.utc).date().isoformat()
    if not JOB.get():
        return today
    step = 'quality_reference_' + stage
    prior = get_optional_artifact(JOB.get(), step)
    meta = (prior or {}).get('meta') or {}
    saved = meta.get('reference_date')
    try:
        valid_date = isinstance(saved, str) and date.fromisoformat(saved).isoformat() == saved
    except ValueError:
        valid_date = False
    if valid_date and (fingerprint is None or meta.get('snapshot') == fingerprint):
        return saved
    if fingerprint is not None:
        upsert_artifact(job_id=JOB.get(), step=step, content_type='application/json',
                        content_text='{}', meta={'snapshot':fingerprint,'reference_date':today})
    return today
