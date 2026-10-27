"""Recheck only named questions, using saved pages before any new collection."""
import copy
import json

from .content_quality import ContentQualityError, digest, source_evidence
from .db import get_artifact, get_optional_artifact, upsert_artifact


def save(job_id,step,value):
    return upsert_artifact(job_id=job_id,step=step,content_type='application/json',
        content_text=json.dumps(value,ensure_ascii=False))


def review(job_id,plan,matrix,pages,ids,findings,round_number,pass_number):
    from .research_requirements import MATRIX_SCHEMA, MATRIX_SYSTEM, validate_matrix
    from .source_spans import indexed_sources, span_schema, expand_references, SPAN_POLICY
    from .tiered_research import checked_request, compact_editorial_plan, packed_sources, validate_visible_matrix
    from .focused_research import related_pages
    questions=[q for q in plan['items'] if q['id'] in ids]
    candidates=[copy.deepcopy(i) for i in matrix['items'] if i['id'] in ids]
    selected=related_pages(pages,candidates)
    packed=packed_sources(selected,candidates,prioritize_referenced=True)
    sources,index=indexed_sources(packed)
    payload={'article_plan':compact_editorial_plan(plan),'plan':{'items':questions},
        'candidate_answers':candidates,'previous_findings':findings,'sources':sources}
    request={'model':'gpt-6.1-sol','max_tokens':18000,'system':MATRIX_SYSTEM+SPAN_POLICY+'''
今回は本文QAで特定された必要回答だけの原文再確認。既存の引継ぎ漏れは保存済み原文から補う。
必須の細目だけが未確認なら、その主張をwithholdとし、独立して確認できる主張は保持する。
質問や重要度を変更せず、提示された質問だけを返す。全体の記事や構成を再生成しない。
''','messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
        'output_config':span_schema(MATRIX_SCHEMA,[q['id'] for q in questions],questions=questions)}
    # A different controller round is not a different source request. Share the
    # provider's exact-request cache/history for this same question set.
    identity=digest(json.dumps(sorted(ids)))[:16]
    value,usage=checked_request(job_id,'tiered_article_upstream_sources_'+identity,request)
    value=expand_references(value,index)
    validate_visible_matrix(value,{'items':questions},packed)
    validate_matrix(value,{'items':questions},pages)
    if {i['id'] for i in value['items']}!=set(ids):
        raise ContentQualityError('局所調査で対象外の質問が変更されています。')
    return value,usage


def run(job_id,keyword,report,question_ids,round_number,api_key=None):
    from .research_requirements import (load_plan, require_authoring_matrix, validate_matrix,
        accepted_facts, matrix_policy, require_valid_references)
    plan=load_plan(job_id);matrix=require_authoring_matrix(job_id)
    ids=set(question_ids)
    if not ids or not ids<={q['id'] for q in plan['items']}:
        raise ContentQualityError('追加確認の対象質問が不正です。全項目へは拡大しません。')
    receipt=get_optional_artifact(job_id,'article_upstream_source_collection')
    history=json.loads(receipt['content_text']) if receipt else {'attempted_question_ids':[],'history':[]}
    if history.get('status')=='running':
        raise ContentQualityError('追加調査の状態が未確定です。同じ要求を再送しません。')
    original=copy.deepcopy(matrix)
    pages=json.loads(get_artifact(job_id,'fresh_sources')['content_text'])
    findings=[f for f in report['editorial_findings'] if f.get('severity')=='blocking'
              and f.get('stage_to_fix')=='research']
    reviewed,_=review(job_id,plan,matrix,pages,ids,findings,round_number,1)
    require_valid_references(reviewed,ids)
    missing={i['id'] for i in reviewed['items'] if i.get('additional_sources_needed') is True}
    collect=missing-set(history['attempted_question_ids'])
    if collect:
        history.update(status='running',question_ids=sorted(collect),
            attempted_question_ids=sorted(set(history['attempted_question_ids'])|collect))
        save(job_id,'article_upstream_source_collection',history)
        from .step_fact_sheet import run as collect_sources
        collect_sources(job_id,keyword,api_key=api_key,research_gaps=json.dumps(findings,ensure_ascii=False),
                        research_question_ids=sorted(collect))
        history['history'].append({'round':round_number,'question_ids':sorted(collect)})
        history['status']='completed';save(job_id,'article_upstream_source_collection',history)
        pages=json.loads(get_artifact(job_id,'fresh_sources')['content_text'])
        reviewed,_=review(job_id,plan,matrix,pages,ids,findings,round_number,2)
        require_valid_references(reviewed,ids)
    replacements={i['id']:i for i in reviewed['items']}
    result={**copy.deepcopy(matrix),'items':[replacements.get(i['id'],i) for i in matrix['items']]}
    gaps=validate_matrix(result,plan,pages)
    facts=accepted_facts(result,plan)
    # Do not relabel a partial recheck as a complete research QA pass.
    result.update(valid=False,gaps=gaps,coverage_sufficient=False,
        policy_sha256=matrix_policy(),plan_sha256=digest(json.dumps(plan,ensure_ascii=False,sort_keys=True)),
        sources_sha256=digest(source_evidence(get_artifact(job_id,'fresh_sources'))),
        attempts_sha256=digest(get_artifact(job_id,'fresh_sources')['content_text']),facts_sha256=digest(facts),
        article_recovery={'round':round_number,'question_ids':sorted(ids),
                          'acceptance':'requires_current_full_article_audit'})
    save(job_id,f'article_upstream_research_before_{round_number}',original)
    before=get_artifact(job_id,'fact_sheet')
    upsert_artifact(job_id=job_id,step='fact_sheet',content_type='text/markdown',content_text=facts,
        meta={**(before.get('meta') or {}),'canonical_research_answers':True})
    save(job_id,f'article_upstream_research_{round_number}',result)
    save(job_id,'research_matrix',result)
    return result
