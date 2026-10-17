"""Give writers accepted decisions, not the entire research document collection."""
import copy
import json

POLICY_VERSION='generation-evidence-v7-reader-progression'
POLICY='''以下は調査の合格後に採用された回答・条件と省略項目。回答の対象・期間・適用条件を維持する。
reader_designの背景・感情・商品適合は設計上の仮説であり検証済み事実ではない。reader_decisionsの主回答と前後関係を保持し、問い・必要な回答・伝え方・読後の状態を構成と本文へ反映する。商品資料の主張や心理仮説から新しい事実を追加せず、確認済みdecisionsの範囲で説明する。
本人の体験・意見は発信者の報告としての限定を保ち、一般的な成功率や効果へ広げない。一般的ハウツーは助言の範囲を保ち、法的判断・安全保証・商業的条件へ変換しない。
引用は回答の根拠であり、引用内の別の情報を自由に追加するための資料ではない。
省略・未確認項目は本文で断定しない。現在の結論に使用不可の情報を現在の比較に使わない。
decisionsのanswerは採用した回答、publication_conditionsは回答と一緒に示す対象・契約・税・期間などの条件、claim_limitsは説明してよい範囲。publishable=falseの論点は掲載しない。supports_current_conclusion=falseの回答で現在の優劣を決めない。claim_limitsは執筆の境界であり、その制約説明を読者へ列挙する文章ではない。
claimsがある場合、各idのtext・subject・operation・conditions・source_idsを一組として扱う。親のpublication_conditionsは集合であり、すべての条件をすべての主張へ付けたり別プランの条件を合成しない。
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
    return {'reader_design':publication_design(plan.get('reader_design',{})),
            'reader_decisions':copy.deepcopy(plan.get('reader_decisions',[])),
            'content_gaps':[{k:copy.deepcopy(g[k]) for k in ('need_id','question','status','opportunity','research_ids')} for g in plan.get('content_gaps',[])],
            'topics': [{k: copy.deepcopy(q[k]) for k in
                       ('id', 'priority_reason') if k in q}
                       for q in plan.get('items', [])]}


def publication_design(design):
    result=copy.deepcopy(design)
    # Adoption/research instructions stay in the reviewed plan, not writing data.
    result.pop('answer_scope_policy',None)
    for need in result.get('reader_needs',[]):
        for field in ('support','confidence_reason','adoption_reason'):need.pop(field,None)
    if result.get('product_fit'):
        result['product_fit']={k:v for k,v in result['product_fit'].items() if k in ('role','need_ids','cta_role')}
    return result


def authoring_intent(job_id, artifact):
    """New analysis supplies reader design; its raw factual examples stay internal."""
    if not (artifact.get('meta') or {}).get('reader_needs_version'):return artifact['content_text']
    from .production_brief import load as load_brief
    from .reader_needs import load, editorial_context
    brief=load_brief(job_id);needs=load(job_id,brief)
    if not brief or not needs:
        from .content_quality import ContentQualityError
        raise ContentQualityError('執筆に使う読者設計がありません。旧分析の数値へ戻しません。')
    return json.dumps(publication_design(editorial_context(brief,needs)),ensure_ascii=False)


def registered_for_authoring(value, *, structured_intent):
    if not value or not structured_intent:return value
    result=copy.deepcopy(value)
    for field in ('selling_points','raw_content','notes'):result.pop(field,None)
    return result


def authoring_serp(text, *, structured_intent):
    if not structured_intent:return text
    value=json.loads(text)
    return json.dumps({'scope':'structure_reference_only',
        'reference_urls':[r['link'] for r in value.get('organic_results',[]) if r.get('link')]},ensure_ascii=False)


def bundle_data(plan, matrix, *, quotes, planning=False):
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
            'additional_sources_needed','reviewed_source_urls',
            'claims','claim_states','claim_evidence','claim_format','claims_checked_at','claim_validation_issue','claim_visibility_failed','claim_policy_issues'}})
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
        row['claims']=[]
        for claim in item.get('claim_states',[]):
            if not claim['publishable'] or not row['publishable']:continue
            published={k:copy.deepcopy(claim[k]) for k in ('id','text','subject','operation','answer_type','conditions','supports_current_conclusion')}
            matched=[]
            for ref in claim['evidence']:
                ref={k:copy.deepcopy(v) for k,v in ref.items() if k not in ('source_kind','independence_group','expert_scope_reason')}
                if not quotes:ref.pop('quote',None);ref.pop('expert_qualification_quote',None)
                matched.append(ref_ids[json.dumps(ref,ensure_ascii=False,sort_keys=True)])
            published['source_ids']=list(dict.fromkeys(matched));row['claims'].append(published)
        rows.append(row)
    bundle={'version':POLICY_VERSION,'scope':{k:v for k,v in plan.items() if k in ('candidate_services',)},
            'decisions':rows,'sources':sources,
            'reader_design':publication_design(plan.get('reader_design',{})),
            'reader_decisions':copy.deepcopy(plan.get('reader_decisions',[]))}
    if planning:
        bundle['editorial_planning']=outline_planning_context(plan)
    return bundle


def decision_bundle(plan, matrix, *, quotes, planning=False):
    return POLICY+'\n'+json.dumps(bundle_data(plan,matrix,quotes=quotes,planning=planning),ensure_ascii=False)


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
    plan.pop('competitor_context',None)
    if plan.get('reader_design'):plan['reader_design']=publication_design(plan['reader_design'])
    if plan.get('reader_design'):
        result.pop('intent_analysis',None);result.pop('intent_value_context',None)
        result['reader_design']=copy.deepcopy(plan['reader_design'])
    if plan.get('content_gaps'):
        plan['content_gaps']=[{k:v for k,v in g.items() if k not in ('evidence','reason')} for g in plan['content_gaps']]
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
        item['claims']=[{k:copy.deepcopy(c[k]) for k in ('id','text','subject','operation','answer_type','conditions',
            'supports_current_conclusion')} for c in item.pop('claim_states',[]) if c['publishable'] and item.get('publishable')]
        for key in ('claim_evidence','claim_format','claims_checked_at','claim_validation_issue','claim_visibility_failed','claim_policy_issues'):item.pop(key,None)
        for ref in item.get('evidence',item.get('evidence_sources',[])):
            for key in ('source_kind','independence_group','expert_scope_reason'):ref.pop(key,None)
    decisions.pop('coverage_reason',None)
    decisions.pop('evidence_ledger',None)
    result['evidence_usage_policy']=POLICY
    return result
