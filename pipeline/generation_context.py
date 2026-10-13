"""Give writers accepted decisions, not the entire research document collection."""
import copy
import json

POLICY_VERSION='generation-evidence-v4-publication-handoff'
POLICY='''以下は調査の合格後に採用された回答・条件と省略項目。回答の対象・期間・適用条件を維持する。
本人の体験・意見は発信者の報告としての限定を保ち、一般的な成功率や効果へ広げない。一般的ハウツーは助言の範囲を保ち、法的判断・安全保証・商業的条件へ変換しない。
引用は回答の根拠であり、引用内の別の情報を自由に追加するための資料ではない。
省略・未確認項目は本文で断定しない。現在の結論に使用不可の情報を現在の比較に使わない。
decisionsのanswerは採用した回答、publication_conditionsは回答と一緒に示す対象・契約・税・期間などの条件、claim_limitsは説明してよい範囲。publishable=falseの論点は掲載しない。supports_current_conclusion=falseの回答で現在の優劣を決めない。claim_limitsは執筆の境界であり、その制約説明を読者へ列挙する文章ではない。
原資料の独立照合は最終の事実確認で実施する。sourcesは出典対応用であり、記事設定で指定された場合だけ出典を表示する。採用経緯の説明を本文に作らない。'''


def publication_scope(item):
    """Use reviewed publication fields, never infer them from audit prose."""
    from .content_quality import ContentQualityError
    result={}
    for field in ('publication_conditions', 'claim_limits'):
        value=item.get(field)
        if not isinstance(value,list) or any(not isinstance(v,str) or not v.strip() for v in value):
            raise ContentQualityError('執筆へ引き継ぐ条件の区分が未確認です。保存資料を再取得せず、既存の調査確認で回答・条件・調査記録の区分を確認してください。自動変換・有料再試行はしません。')
        result[field]=copy.deepcopy(value)
    return result


def outline_planning_context(plan):
    """Editorial relevance is separate from accepted facts and source bookkeeping."""
    return {'topics': [{k: copy.deepcopy(q[k]) for k in
                       ('id', 'priority_reason') if k in q}
                       for q in plan.get('items', [])]}


def decision_bundle(plan, matrix, *, quotes, planning=False):
    """Project current-policy answers; callers also validate source/plan snapshots."""
    from .content_quality import ContentQualityError
    from .research_requirements import matrix_policy
    from .quality_warnings import enabled, warn
    policy_matches = matrix.get('policy_sha256') == matrix_policy()
    if not policy_matches:
        if not enabled(): raise ContentQualityError('旧方針の調査回答は執筆資料に使えません。採用経緯と回答を分離して再確認してください。')
        warn('調査回答の方針が一致していません。未確認の回答は使用しません。')
    if matrix.get('valid') is not True:
        if not enabled(): raise ContentQualityError('未合格の調査を執筆用資料へ変換できません。')
        warn('調査に不足があります。確認済みの回答だけを使用します。')
    questions={q['id']:q for q in plan['items']}
    items = matrix.get('items', [])
    if not isinstance(items, list):
        raise ContentQualityError('調査回答の形式が不正です。')
    if len(items)!=len(questions) or {i['id'] for i in items}!=set(questions):
        if not enabled(): raise ContentQualityError('執筆資料の質問と回答が一致しません。')
        warn('質問と回答に不一致があります。不明な回答は使用しません。')
    rows=[];sources={};ref_ids={};constraints={}
    for item in items:
        if not policy_matches or not item.get('verified') or item['id'] not in questions:
            if not enabled(): raise ContentQualityError('未確認の回答を執筆用資料へ渡せません。')
            continue
        row={k:copy.deepcopy(v) for k,v in questions[item['id']].items()
             if k not in {'priority_reason','source_requirement'}}
        # Preserve unknown future answer/condition fields; drop only review bookkeeping.
        row.update({k:copy.deepcopy(v) for k,v in item.items() if k not in {
            'reason','basis','applicable_at','omission_reason','verified','evidence','official_checked_urls','exploration_complete','exploration_reason',
            'omission_candidate_from','omission_source_review','unresolved_candidate',
            'additional_sources_needed','reviewed_source_urls'}})
        row.update(publication_scope(item))
        row['publishable']=item.get('basis')!='omitted' and item.get('status') in ('confirmed','explicitly_undisclosed')
        row['source_ids']=[]
        if item.get('basis')!='omitted':
            for ref in item.get('evidence',[]):
                ref={k:copy.deepcopy(v) for k,v in ref.items()
                     if k not in {'source_kind','independence_group','expert_scope_reason'}}
                if not quotes:
                    ref.pop('quote',None);ref.pop('expert_qualification_quote',None)
                key=json.dumps(ref,ensure_ascii=False,sort_keys=True)
                if key not in ref_ids:
                    name='ref'+str(len(ref_ids)+1);ref_ids[key]=name;sources[name]=ref
                row['source_ids'].append(ref_ids[key])
        rows.append(row)
    bundle={'version':POLICY_VERSION,'scope':{k:v for k,v in plan.items() if k in ('candidate_services',)},
            'decisions':rows,'sources':sources}
    if planning:
        bundle['editorial_planning']=outline_planning_context(plan)
    return POLICY+'\n'+json.dumps(bundle,ensure_ascii=False)


def generation_evidence(job_id, facts, sources, *, quotes=True, planning=False):
    from .research_requirements import require_matrix,load_plan
    # Every writing route uses the same validated evidence boundary.
    current=require_matrix(job_id)
    return decision_bundle(load_plan(job_id),current,quotes=quotes,planning=planning)


def repair_requirements(requirements, *, planning=False):
    """Project an already source-scoped repair packet; do not change audit inputs."""
    result=copy.deepcopy(requirements)
    # Legacy prose constraints are audit records even without a decision bundle.
    result.pop('usage_constraints',None)
    plan=result.get('research_plan',{})
    plan.pop('scope_reason',None)
    for question in plan.get('items',[]):
        if not planning:
            question.pop('priority_reason',None)
        question.pop('source_requirement',None)
    decisions=result.get('research_decisions')
    if not isinstance(decisions,dict):return result
    for item in decisions.get('items',[]):
        item.update(publication_scope(item))
        if 'publishable' not in item:
            item['publishable']=item.get('basis')!='omitted' and item.get('status') in ('confirmed','explicitly_undisclosed')
        for key in ('basis','reason','applicable_at','omission_reason','verified','exploration_reason',
                    'exploration_complete','official_checked_urls','omission_candidate_from','omission_source_review',
                    'unresolved_candidate','reviewed_source_urls','additional_sources_needed'):
            item.pop(key,None)
        for ref in item.get('evidence',item.get('evidence_sources',[])):
            for key in ('source_kind','independence_group','expert_scope_reason'):ref.pop(key,None)
    decisions.pop('coverage_reason',None)
    result['evidence_usage_policy']=POLICY
    return result
