"""Rejudge only malformed journey citations using selectable source spans."""
import copy
import json
from types import SimpleNamespace

from .content_quality import ContentQualityError, digest
from . import reader_journey as journey


def invalid_rows(value, ctx, text):
    from .editorial_blueprint import journey_evidence
    rows=value.get('reader_progression_reviews')
    if not isinstance(rows,dict) or set(rows)!=set(ctx['progression_section_ids']):
        raise ContentQualityError('章の確認件数が不整合です。引用の再確認では修復できません。')
    evidence=journey_evidence(ctx,text);invalid={}
    for sid,row in rows.items():
        if not isinstance(row,dict) or set(row)!={'status','reason','journey'}:
            raise ContentQualityError('章の確認形式が不整合です。')
        scope=journey.chapter_evidence(sid,evidence)
        try:journey.checked(row['journey'],scope)
        except ContentQualityError:
            # Canonical section fields only. Display aliases duplicate these
            # fields and cannot add psychology/outcome metadata as support.
            canonical=journey.chapter_evidence(sid,ctx['journey_sections'])
            spans={f'{key}.text{n:03d}':{'id':key,'quote':t}
                   for key,parts in canonical.items() for n,t in enumerate(parts,1) if t.strip()}
            if not spans:raise ContentQualityError('章の再確認に使える執筆指示がありません。')
            invalid[sid]={'previous_review':copy.deepcopy(row),'spans':spans}
    return invalid


def output_config(rows):
    def obj(fields):return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
    fields={}
    for sid,entry in rows.items():
        shape=journey.schema()
        for key in ('attunement','lived_value','guidance'):
            shape['properties'][key]['properties']['evidence']={
                'type':'array','items':{'type':'string','enum':list(entry['spans'])}}
        fields[sid]=obj({'status':{'type':'string','enum':['pass','answer_missing','evidence_missing']},
                         'reason':{'type':'string'},'journey':shape})
    return {'format':{'type':'json_schema','schema':obj(fields)}}


def apply(value, response, rows, ctx, text):
    from .editorial_blueprint import checked_progression_reviews, validate_shape
    validate_shape(response,output_config(rows)['format']['schema'])
    result=copy.deepcopy(value)
    for sid,row in response.items():
        row=copy.deepcopy(row);previous=rows[sid]['previous_review']
        if previous['status']!='pass' and row['status']!=previous['status']:
            raise ContentQualityError('引用の再確認で元の内容不合格を解除できません。')
        for key in ('attunement','lived_value','guidance'):
            if previous['journey'][key]['status']=='fail' and row['journey'][key]['status']!='fail':
                raise ContentQualityError('引用の再確認で元の不足を解除できません。')
            row['journey'][key]['evidence']=[copy.deepcopy(rows[sid]['spans'][ref])
                                           for ref in row['journey'][key]['evidence']]
        result['reader_progression_reviews'][sid]=row
    checked_progression_reviews(result,ctx,text=text)
    return result


def recheck(job_id, value, ctx, text):
    rows=invalid_rows(value,ctx,text)
    if not rows:return value,SimpleNamespace(input_tokens=0,output_tokens=0),None
    from .tiered_research import checked_request
    request=dict(model='gpt-6.1-sol',max_tokens=6000,output_config=output_config(rows),
        system=journey.REVIEW_POLICY+'''
保存済み構成QAのうち引用検査で不整合だった章だけを再確認する。記事・構成の修正や全体QAの再実行ではない。
previous_reviewの判定は参考であり、不正な引用を機械的に別の引用へ付け替えない。
提示spansは同じ章と配下の結論・回答要素・具体例の原文だけ。心理欄・読後状態・導線メモを引用根拠として追加しない。
各evidenceは、その働きを実際に裏付けるspanのIDだけを選ぶ。本文文字列を生成しない。
元の不足判定(status!=pass、各aspectのfail)は解除しない。新たに裏付けられないpassはfailにできる。
当該章が新たな判断を担わず、子節で意味が具体化される導入等は理由を説明してnot_neededにできるが、元のfailをnot_neededへ変えない。
他章・全体check・個別必要回答の判定は変更しない。''',
        messages=[{'role':'user','content':json.dumps({'outline_sha256':digest(text),
            'reader_focus':ctx.get('reader_focus',{}),'rows':rows},ensure_ascii=False)}])
    response,usage=checked_request(job_id,'outline_journey_reference_recheck',request)
    result=apply(value,response,rows,ctx,text)
    trace={'section_ids':list(rows),'outline_sha256':digest(text),
           'original_review_sha256':digest(json.dumps(value,ensure_ascii=False,sort_keys=True)),
           'rechecked_review_sha256':digest(json.dumps(result,ensure_ascii=False,sort_keys=True))}
    return result,usage,trace
