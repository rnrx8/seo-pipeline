"""Give writers accepted decisions, not the entire research document collection."""
import copy
import json

POLICY_VERSION='generation-evidence-v2-separated-provenance'
POLICY='''以下は調査の合格後に採用された回答・条件と省略項目。回答の対象・期間・適用条件を維持する。
引用は回答の根拠であり、引用内の別の情報を自由に追加するための資料ではない。
省略・未確認項目は本文で断定しない。現在の結論に使用不可の情報を現在の比較に使わない。
decisionsは執筆する回答と条件。usage_constraintsは回答の使用範囲を判断するための記録で、本文用の文章ではない。実際の対象期間・利用条件だけを回答と対応させ、取得日を適用日へ変換しない。
原資料の独立照合は最終の事実確認で実施する。sourcesは出典対応用であり、記事設定で指定された場合だけ出典を表示する。採用経緯の説明を本文に作らない。'''


def decision_bundle(plan, matrix, *, quotes):
    """Project current-policy answers; callers also validate source/plan snapshots."""
    from .content_quality import ContentQualityError
    from .research_requirements import matrix_policy
    if matrix.get('policy_sha256') != matrix_policy():
        raise ContentQualityError('旧方針の調査回答は執筆資料に使えません。採用経緯と回答を分離して再確認してください。')
    if matrix.get('valid') is not True:
        raise ContentQualityError('未合格の調査を執筆用資料へ変換できません。')
    questions={q['id']:q for q in plan['items']}
    if len(matrix['items'])!=len(questions) or {i['id'] for i in matrix['items']}!=set(questions):
        raise ContentQualityError('執筆資料の質問と回答が一致しません。')
    rows=[];sources={};ref_ids={};constraints={}
    for item in matrix['items']:
        if not item.get('verified'):
            raise ContentQualityError('未確認の回答を執筆用資料へ渡せません。')
        row={k:copy.deepcopy(v) for k,v in questions[item['id']].items()
             if k not in {'priority_reason','source_requirement'}}
        # Preserve unknown future answer/condition fields; drop only review bookkeeping.
        row.update({k:copy.deepcopy(v) for k,v in item.items() if k not in {
            'reason','basis','applicable_at','omission_reason','verified','evidence','official_checked_urls','exploration_complete','exploration_reason',
            'omission_candidate_from','omission_source_review','unresolved_candidate',
            'additional_sources_needed','reviewed_source_urls'}})
        constraints[item['id']]={k:copy.deepcopy(item[k]) for k in ('applicable_at','omission_reason') if item.get(k)}
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
    return POLICY+'\n'+json.dumps({'version':POLICY_VERSION,'scope':{k:v for k,v in plan.items() if k in ('candidate_services',)},
                                  'decisions':rows,'usage_constraints':constraints,'sources':sources},ensure_ascii=False)


def generation_evidence(job_id, facts, sources, *, quotes=True):
    from .research_requirements import require_matrix,load_plan
    # Every writing route uses the same validated evidence boundary.
    current=require_matrix(job_id)
    return decision_bundle(load_plan(job_id),current,quotes=quotes)


def repair_requirements(requirements):
    """Project an already source-scoped repair packet; do not change audit inputs."""
    result=copy.deepcopy(requirements)
    decisions=result.get('research_decisions')
    if not isinstance(decisions,dict):return result
    constraints=copy.deepcopy(result.get('usage_constraints',{}))
    for item in decisions.get('items',[]):
        constraints.setdefault(item['id'],{}).update({k:copy.deepcopy(item[k]) for k in ('applicable_at','omission_reason') if item.get(k)})
        if 'publishable' not in item:
            item['publishable']=item.get('basis')!='omitted' and item.get('status') in ('confirmed','explicitly_undisclosed')
        for key in ('basis','reason','applicable_at','omission_reason','verified','exploration_reason',
                    'exploration_complete','official_checked_urls','omission_candidate_from','omission_source_review',
                    'unresolved_candidate','reviewed_source_urls','additional_sources_needed'):
            item.pop(key,None)
        for ref in item.get('evidence',item.get('evidence_sources',[])):
            for key in ('source_kind','independence_group','expert_scope_reason'):ref.pop(key,None)
    decisions.pop('coverage_reason',None)
    result['usage_constraints']=constraints
    result['evidence_usage_policy']=POLICY
    plan=result.get('research_plan',{})
    plan.pop('scope_reason',None)
    for question in plan.get('items',[]):
        question.pop('priority_reason',None);question.pop('source_requirement',None)
    return result
