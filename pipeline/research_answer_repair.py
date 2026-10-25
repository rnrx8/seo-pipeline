"""Bounded source-bound answer repairs with independent full coverage review."""
import copy
import json
from types import SimpleNamespace

VERSION = 'verified-answer-repair-v2-progressive'
MAX_ROUNDS = 3
POLICY = '''
全体確認で指定された引継ぎ・説明の不足だけを修正する。新しい資料収集はしない。
既存回答や不足理由は参考であり、採用する事実と条件は提示原文で照合する。
根拠のある補助的な主張もURL・原文との対応を保持する。根拠を示せない補助情報は、主要な回答が成立する場合だけ省略する。
editorialの不足には、確認した事実を前提に、読者が選べる案・確認手順とその限界を具体化する。
実行可能な案や設計者への確認事項はproposal、確認した前提からの判断はinferenceとして区別する。
提案と同じ文が資料にないことだけで提案を削らない。一方、未確認の仕様・施工可否・価格・効果を事実として補わない。
指定された質問内の集計・分類・比較は、指摘された行だけでなく回答全体で同じ基準を適用して整合を確認する。別の行の同種の誤りを残さない。算出・分類した値は原典の統計値と区別する。
計画の質問、重要度、対象、必須の回答範囲を変更しない。原文にない条件や根拠を作らない。
'''


def eligible(value, plan, pages):
    from .research_requirements import validate_matrix
    rounds=value.get('answer_repair_rounds', 1 if value.get('answer_repair_version') else 0)
    if rounds >= MAX_ROUNDS or value.get('answer_repair_stopped_reason') or value.get('coverage_sufficient') is not False:
        return False
    issues = value.get('coverage_issues', [])
    ids = {q['id'] for q in plan['items']}
    if not issues or any(i.get('id') not in ids or i.get('repair_kind') not in ('handoff', 'editorial') for i in issues):
        return False
    candidate = copy.deepcopy(value)
    validate_matrix(candidate, plan, pages)
    return ({i['id'] for i in candidate['items']} == ids
            and all(i.get('verified') for i in candidate['items']))


def _run_once(job_id, value, plan, pages, intent_context):
    from .content_quality import ContentQualityError
    from .research_requirements import MATRIX_SCHEMA, MATRIX_SYSTEM, validate_matrix
    from .tiered_research import (checked_request, compact_editorial_plan, packed_sources,
                                 review_coverage, validate_visible_matrix)
    from .source_spans import indexed_sources, span_schema, expand_references, SPAN_POLICY

    if not eligible(value, plan, pages):
        raise ContentQualityError('回答修正は根拠確認済みの引継ぎ・説明不足だけが対象です。')
    result = copy.deepcopy(value)
    round_number=value.get('answer_repair_rounds', 1 if value.get('answer_repair_version') else 0)+1
    inputs = outputs = 0
    # Correct the presentation of existing evidence before buying any repair.
    overall, usage = review_coverage(job_id, plan, pages, result['items'], intent_context)
    inputs += usage.input_tokens; outputs += usage.output_tokens
    result.update(overall)
    if eligible(result, plan, pages):
        targets = {i['id'] for i in result['coverage_issues']}
        candidates = [i for i in result['items'] if i['id'] in targets]
        questions = [q for q in plan['items'] if q['id'] in targets]
        packed = packed_sources(pages, candidates, prioritize_referenced=True)
        sources, source_index = indexed_sources(packed)
        payload = {'article_plan':compact_editorial_plan(plan), 'plan':{'items':questions},
                   'candidate_answers':candidates, 'previous_findings':result['coverage_issues'],
                   'related_verified_answers':[{k:i.get(k) for k in ('id','answer','publication_conditions','claim_limits')} for i in result['items'] if i['id'] not in targets],
                   'sources':sources}
        request = dict(model='gpt-6.1-sol', max_tokens=16000,
                       system=MATRIX_SYSTEM + SPAN_POLICY + POLICY,
                       output_config=span_schema(MATRIX_SCHEMA, [q['id'] for q in questions], questions=questions),
                       messages=[{'role':'user','content':json.dumps(payload, ensure_ascii=False)}])
        from .tiered_research import stable_review_request
        request=stable_review_request(request)
        step='research_answer_repair_candidate'+('' if round_number==1 else '_'+str(round_number))
        repaired, usage = checked_request(job_id, step, request)
        inputs += usage.input_tokens; outputs += usage.output_tokens
        repaired = expand_references(repaired, source_index)
        validate_visible_matrix(repaired, {'items':questions}, packed)
        if {i['id'] for i in repaired['items']} != targets:
            raise ContentQualityError('回答修正の対象IDが一致しません。')
        replacements = {i['id']:i for i in repaired['items']}
        result['items'] = [replacements.get(i['id'], i) for i in result['items']]
        # The repairer's verdict never grants article-wide acceptance.
        overall, usage = review_coverage(job_id, plan, pages, result['items'], intent_context)
        inputs += usage.input_tokens; outputs += usage.output_tokens
        result.update(overall)
    result['answer_repair_version'] = VERSION
    result['answer_repair_rounds'] = round_number
    validate_matrix(result, plan, pages)
    return result, SimpleNamespace(input_tokens=inputs, output_tokens=outputs)


def run(job_id, value, plan, pages, intent_context):
    """Repair evolving findings, never replay unchanged failures indefinitely."""
    from .content_quality import digest, ContentQualityError
    if not eligible(value,plan,pages):
        raise ContentQualityError('回答修正は未解決の新しい引継ぎ・説明不足だけが対象です。')
    def findings(v):
        return digest(json.dumps(v.get('coverage_issues',[]),ensure_ascii=False,sort_keys=True))
    def answers(v):
        return digest(json.dumps([{k:i.get(k) for k in ('id','answer','publication_conditions','claim_limits','claims','evidence')}
                                  for i in v['items']],ensure_ascii=False,sort_keys=True))
    current=copy.deepcopy(value)
    history=copy.deepcopy(value.get('answer_repair_history',[]))
    inputs=outputs=0
    while eligible(current,plan,pages):
        before=findings(current);answer_before=answers(current)
        if any(h['findings_sha256']==before for h in history):
            current['answer_repair_stopped_reason']='repeated_findings'
            break
        result,usage=_run_once(job_id,current,plan,pages,intent_context)
        inputs+=usage.input_tokens;outputs+=usage.output_tokens
        history.append({'round':result['answer_repair_rounds'],'findings_sha256':before,
                        'answer_before_sha256':answer_before,'answer_after_sha256':answers(result),
                        'coverage_sufficient':result.get('coverage_sufficient') is True})
        result['answer_repair_history']=copy.deepcopy(history)
        current=result
        if current.get('coverage_sufficient') is not True and (findings(current)==before or answers(current)==answer_before):
            current['answer_repair_stopped_reason']='unchanged_findings_or_answers'
            break
    return current,SimpleNamespace(input_tokens=inputs,output_tokens=outputs)
