"""Important claims are the source of truth; answer summaries are projections."""
import copy
from datetime import datetime, timezone
from .content_quality import ContentQualityError

VERSION = 'claim-evidence-v1'
POLICY = '''
【重要な主張を正とする回答形式】
今回の出力はclaims。answer・publication_conditions・answer_type・basis・status・supports_current_conclusionの質問単位の集計はシステムが作るため出力しない。既存のanswerへの指示は各claimのtextとconditionsへ適用する。
1つの質問でも、読者の判断を変える対象・動作・プラン・期間・金額単位・例外が異なる重要な主張は分ける。一般的な文章を一文ずつ分解したり、同じ意味の主張を量産しない。各主張のsubjectとoperationで何の誰のどの動作かを保ち、送信と閲覧・月額換算と契約総額等を入れ替えない。
conditionsは読者が利用判断に必要な実条件だけ。出典の取得日・採用経緯・不明項目の説明を混ぜない。実際の対象時点はtime、取得時点はシステムの資料記録で区別する。
evidence_indicesは同じ質問のevidence配列の0始まり番号。主張そのもの（inferenceは前提）を支える資料だけを対応させる。異なるページの条件を合成するときは対象・プラン・時点を照合する。
conflictはnone/resolved/unresolved。食い違う取得資料があればevidence_indicesとresolutionへ記録し、対象・条件・時点の違いを説明できるときだけresolvedにする。ページ種別が概要やFAQという理由だけで一方を優先せず、矛盾が残る主張はwithholdとする。非公表と取得失敗・未探索を混同しない。
dispositionはpublish/qualified/withhold/omit。qualifiedは適用条件を限定した説明であり、未解決の主張に「かもしれない」を付けて掲載することではない。required_for_answerには主回答・結論に不可欠かを指定する。補助的な番号の矛盾だけで、確認できた運営者・機能全体を失わせない。
sourced/proposal/inferenceの区別と出典基準は共通方針に従う。proposalのbasisはguidanceとし、質問に商業情報が含まれていても日常的な確認手順・判断の提案は区別する。未確認の仕様・料金・現行規約・専門判断を提案に付け替えない。すべてを出典の事実として扱わず、提案を商業条件や専門判断の抜け道にしない。claim_limitsは質問全体に対する内部の執筆境界。本文用の免責文ではない。
'''


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def array(item): return {'type':'array','items':item}
STR = {'type':'string'}
INDEX = {'type':'integer','minimum':0}
CLAIM_SCHEMA = obj({
    'text':{'type':'string','minLength':1},
    'subject':{'type':'string','minLength':1},
    'operation':{'type':'string','minLength':1},
    'answer_type':{'type':'string','enum':['sourced','proposal','inference']},
    'basis':{'type':'string','enum':['primary','guidance','expert','corroborated','historical','unresolved','omitted']},
    'supports_current_conclusion':{'type':'boolean'},
    'conditions':array(obj({'dimension':{'type':'string','enum':['audience','plan','term','price_basis','time','exception','other']},'value':STR})),
    'evidence_indices':array(INDEX), 'required_for_answer':{'type':'boolean'},
    'disposition':{'type':'string','enum':['publish','qualified','withhold','omit']},
    'conflict':obj({'status':{'type':'string','enum':['none','resolved','unresolved']},
                    'evidence_indices':array(INDEX),'resolution':STR}),
})


def response_schema(legacy_schema):
    result = copy.deepcopy(legacy_schema)
    row = result['format']['schema']['properties']['items']['items']
    for key in ('answer','publication_conditions','answer_type','basis','status','supports_current_conclusion'):
        row['properties'].pop(key,None)
        if key in row['required']: row['required'].remove(key)
    row['properties']['claims'] = array(CLAIM_SCHEMA)
    row['required'].append('claims')
    return result


REFERENCE_ISSUE = 'invalid_claim_reference'
PROBLEM_LABELS = {'not_list':'配列ではない','non_integer':'整数以外','out_of_range':'範囲外','duplicate':'重複'}


def reference_problems(indices, evidence):
    if not isinstance(indices,list): return ['not_list']
    numbers=[i for i in indices if type(i) is int]
    problems=['non_integer'] if len(numbers)!=len(indices) else []
    if any(not 0<=i<len(evidence) for i in numbers): problems.append('out_of_range')
    if len(numbers)!=len(set(numbers)): problems.append('duplicate')
    return problems


def reference_issue(claim_id, claim, evidence):
    """Record the rejected response verbatim; never guess, clamp, or drop a reference."""
    conflict=claim['conflict']
    fields=[{'field':field,'indices':copy.deepcopy(indices),'problems':problems}
            for field,indices in (('evidence_indices',claim.get('evidence_indices')),
                                  ('conflict.evidence_indices',conflict.get('evidence_indices')))
            if (problems:=reference_problems(indices,evidence))]
    if not fields: return None
    return {'code':REFERENCE_ISSUE,'claim_id':claim_id,'evidence_count':len(evidence),'fields':fields}


def reference_reason(issue):
    count=issue['evidence_count']
    valid=f'有効な番号は0〜{count-1}' if count else '参照できる根拠がありません'
    detail='、'.join(f"{f['field']}={f['indices']!r}（{'・'.join(PROBLEM_LABELS[p] for p in f['problems'])}）" for f in issue['fields'])
    return (f'主張の根拠参照が不正です：{detail}。evidenceは{count}件で{valid}。'
            '参照を推測で補正しないため、この主張は原文IDから再判定が必要です。')


def reference_failures(matrix, ids=None):
    """Question IDs whose claim/evidence mapping was rejected; this is not missing source material."""
    return sorted({item['id'] for item in (matrix or {}).get('items',[]) if isinstance(item,dict)
                   and (ids is None or item.get('id') in ids)
                   and any(i.get('code')==REFERENCE_ISSUE for i in item.get('claim_policy_issues',[]))})


def assess(item, question, pages):
    """Reuse the existing source-basis validator per important claim, not per paragraph."""
    if 'claims' not in item: return
    from .research_requirements import validate_matrix
    claims = item['claims']
    evidence = item.get('claim_evidence',item.get('evidence'))
    if not isinstance(claims,list) or not isinstance(evidence,list):
        raise ContentQualityError('主張と根拠の形式が不正です。')
    if len(claims)>24: raise ContentQualityError('一つの質問の主張が過大です。重要な判断単位へ整理してください。')
    omitted=bool(item.get('basis')=='omitted' and item.get('omission_candidate_from'))
    rows=[];accepted_indices=[];critical_missing=[];conditions=[];policy_issues=[]
    for index,claim in enumerate(claims):
        if (not isinstance(claim,dict) or any(not isinstance(claim.get(k),str) or not claim[k].strip() for k in ('text','subject','operation'))
            or claim.get('answer_type') not in ('sourced','proposal','inference')
            or claim.get('basis') not in ('primary','guidance','expert','corroborated','historical','unresolved','omitted')
            or type(claim.get('required_for_answer')) is not bool or type(claim.get('supports_current_conclusion')) is not bool
            or claim.get('disposition') not in ('publish','qualified','withhold','omit')):
            raise ContentQualityError('重要な主張の対象・動作・掲載判断が不正です。')
        scope=claim.get('conditions')
        if not isinstance(scope,list) or any(not isinstance(c,dict) or c.get('dimension') not in (
            'audience','plan','term','price_basis','time','exception','other') or not isinstance(c.get('value'),str) or not c['value'].strip() for c in scope):
            raise ContentQualityError('主張の適用条件が不正です。')
        conflict=claim.get('conflict')
        if not isinstance(conflict,dict) or conflict.get('status') not in ('none','resolved','unresolved') or not isinstance(conflict.get('resolution'),str):
            raise ContentQualityError('資料間の矛盾の確認状態が不正です。')
        claim_id=f"{question['id']}.c{index+1:02d}"
        # A malformed mapping is one rejected claim, not a stop for every other
        # answer. Keep the original response and exact reason for the bounded
        # re-review; the whole question stays unverified until a new response.
        issue=reference_issue(claim_id,claim,evidence)
        if issue:
            reason=reference_reason(issue)
            rows.append({**copy.deepcopy(claim),'id':claim_id,'publishable':False,'evidence':[],'conflicting_evidence':[],
                         'validation_reason':reason,'reference_issue':issue})
            policy_issues.append(issue);critical_missing.append(claim_id+'：'+reason)
            continue
        if conflict['status']!='none' and (not conflict['evidence_indices'] or not conflict['resolution'].strip()):
            raise ContentQualityError('資料の矛盾に参照箇所・判断理由がありません。')
        refs=[copy.deepcopy(evidence[i]) for i in claim['evidence_indices']]
        # No claims field in this mini-record: the legacy source validator is the leaf.
        mini={k:copy.deepcopy(v) for k,v in item.items() if k not in (
            'claims','claim_states','claim_evidence','coverage_issues','claims_checked_at','claim_policy_issues',
            'claim_validation_issue','validation_issue','verified')}
        mini.update(id=question['id'],answer=claim['text'],answer_type=claim['answer_type'],basis=claim['basis'],
                    status='confirmed',evidence=refs,publication_conditions=[c['value'] for c in scope],
                    supports_current_conclusion=claim['supports_current_conclusion'],
                    claim_limits=item.get('claim_limits',[]))
        gaps=validate_matrix({'items':[mini],'coverage_sufficient':True,'coverage_reason':'individual claim'},
                              {'items':[question]},pages)
        publishable=(not gaps and mini['verified'] and conflict['status']!='unresolved'
                     and claim['disposition'] in ('publish','qualified'))
        visible_failure=claim_id in item.get('claim_visibility_failed',[])
        pending_sources=item.get('additional_sources_needed') is True
        if visible_failure or pending_sources: publishable=False
        # A resolved conflict must refer to real fetched text as well as supporting evidence.
        available={p['url']:p.get('text','') for p in pages if p.get('status','success')=='success'}
        conflict_valid=all(evidence[i].get('url') in available and
            ''.join(evidence[i].get('quote','').split()) in ''.join(available[evidence[i]['url']].split())
            and bool(evidence[i].get('quote','').strip()) for i in conflict['evidence_indices'])
        if not conflict_valid: publishable=False
        reason=('照合担当が追加の保存済み資料を必要としています。' if pending_sources else
                '今回の照合で原文を確認できていません。新しい照合が必要です。' if visible_failure else
                '資料の矛盾が未解決' if conflict['status']=='unresolved' else
                '矛盾を判断した原文が未取得・不一致' if not conflict_valid else
                '\n'.join(g['reason'] for g in gaps) if gaps else
                '掲載を保留・省略' if claim['disposition'] in ('withhold','omit') else '根拠・条件を照合')
        row={**copy.deepcopy(claim),'id':claim_id, 'publishable':publishable,
             'evidence':refs,'conflicting_evidence':[copy.deepcopy(evidence[i]) for i in conflict['evidence_indices']],
             'validation_reason':reason}
        if mini.get('validation_issue'):
            row['validation_issue']=copy.deepcopy(mini['validation_issue'])
            if claim['required_for_answer']:policy_issues.append(row['validation_issue'])
        rows.append(row)
        if publishable:
            accepted_indices.extend(claim['evidence_indices']);conditions.extend(c['value'] for c in scope)
        elif claim['required_for_answer']: critical_missing.append(row['id']+'：'+reason)
    published=[r for r in rows if r['publishable']]
    item['claim_evidence']=copy.deepcopy(evidence)
    item['claim_states']=rows
    item['claim_policy_issues']=policy_issues
    item.setdefault('claims_checked_at',datetime.now(timezone.utc).isoformat())
    item['claim_format']=VERSION
    item['answer']='\n'.join(r['text'] for r in published)
    item['publication_conditions']=list(dict.fromkeys(conditions))
    item['answer_type']='proposal' if published and all(r['answer_type']=='proposal' for r in published) else (
        'inference' if any(r['answer_type']=='inference' for r in published) else 'sourced')
    item['basis']=published[0]['basis'] if published else 'unresolved'
    item['supports_current_conclusion']=bool(published) and all(r['supports_current_conclusion'] for r in published)
    item['evidence']=[copy.deepcopy(evidence[i]) for i in sorted(set(accepted_indices))]
    item['status']='confirmed' if published and not critical_missing else 'unresearched'
    if omitted and not published:
        item.update(basis='omitted',status='searched_not_found',answer='',evidence=[],supports_current_conclusion=False)
    if critical_missing:
        item['claim_validation_issue']='\n'.join(critical_missing)
    else: item.pop('claim_validation_issue',None)


def ledger(value,pages):
    """Display/export projection, with source acquisition time kept distinct from applicability."""
    fetched={p['url']:p.get('fetched_at') for p in pages}
    result=[]
    for item in value['items']:
        for row in item.get('claim_states',[]):
            result.append({**copy.deepcopy(row),'question_id':item['id'],
                'publishable':bool(item.get('verified') and row['publishable']),
                'checked_at':item.get('claims_checked_at'),
                'evidence':[{**copy.deepcopy(r),'fetched_at':fetched.get(r['url'])} for r in row['evidence']]})
    return {'version':VERSION,'claims':result}
