"""Give writers accepted decisions, not the entire research document collection."""
import copy
import json

POLICY_VERSION='generation-evidence-v1'
POLICY='''以下は調査の合格後に採用された回答・条件と省略項目。回答の対象・期間・適用条件を維持する。
引用は回答の根拠であり、引用内の別の情報を自由に追加するための資料ではない。
省略・未確認項目は本文で断定しない。現在の結論に使用不可の情報を現在の比較に使わない。
原資料の独立照合は最終の事実確認で実施する。出典IDは内部参照であり、記事には出典URLを使用する。'''


def decision_bundle(plan, matrix, *, quotes):
    """Pure projection: callers must validate the stored matrix before use."""
    from .content_quality import ContentQualityError
    if matrix.get('valid') is not True:
        raise ContentQualityError('未合格の調査を執筆用資料へ変換できません。')
    questions={q['id']:q for q in plan['items']}
    if len(matrix['items'])!=len(questions) or {i['id'] for i in matrix['items']}!=set(questions):
        raise ContentQualityError('執筆資料の質問と回答が一致しません。')
    rows=[];sources={};ref_ids={}
    for item in matrix['items']:
        if not item.get('verified'):
            raise ContentQualityError('未確認の回答を執筆用資料へ渡せません。')
        row={k:copy.deepcopy(v) for k,v in questions[item['id']].items()}
        # Preserve unknown future answer/condition fields; drop only review bookkeeping.
        row.update({k:copy.deepcopy(v) for k,v in item.items() if k not in {
            'evidence','official_checked_urls','exploration_complete','exploration_reason',
            'omission_candidate_from','omission_source_review','unresolved_candidate',
            'additional_sources_needed','reviewed_source_urls'}})
        row['source_ids']=[]
        if item.get('basis')!='omitted':
            for ref in item.get('evidence',[]):
                ref=copy.deepcopy(ref)
                if not quotes:
                    ref.pop('quote',None);ref.pop('expert_qualification_quote',None)
                key=json.dumps(ref,ensure_ascii=False,sort_keys=True)
                if key not in ref_ids:
                    name='ref'+str(len(ref_ids)+1);ref_ids[key]=name;sources[name]=ref
                row['source_ids'].append(ref_ids[key])
        rows.append(row)
    return POLICY+'\n'+json.dumps({'version':POLICY_VERSION,'scope':{k:v for k,v in plan.items() if k in ('candidate_services','scope_reason')},
                                  'decisions':rows,'sources':sources},ensure_ascii=False)


def generation_evidence(job_id, facts, sources, *, quotes=True):
    from .quality_context import compact_enabled
    from .content_quality import writing_evidence
    if not compact_enabled():return writing_evidence(facts,sources)
    from .research_requirements import require_matrix,load_plan
    # A valid flag alone is insufficient: require_matrix checks source/fact/policy revisions.
    current=require_matrix(job_id)
    return decision_bundle(load_plan(job_id),current,quotes=quotes)
