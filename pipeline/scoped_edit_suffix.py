"""Recover complete paragraph edits; never close a truncated JSON string."""
import copy,json
from .content_quality import ContentQualityError,digest
from . import editorial_blueprint as bp
from .outline_completion import prefix

def recover(job_id,request,artifact,packet,client):
    from .editorial_contract import enrich_request
    from .tiered_research import checked_request
    request=enrich_request(request);fingerprint=digest(json.dumps(request,ensure_ascii=False,sort_keys=True))
    if artifact['meta'].get('request_sha256')!=fingerprint or artifact['meta'].get('stop_reason')!='max_tokens':
        raise ContentQualityError('保存済みの未完成編集と現在の依頼が不一致です。再送しません。')
    schema=request['output_config']['format']['schema']
    fixed,key,head,remaining,fragment=prefix(artifact['content_text'],schema,preserved_array='article_edits')
    for name,value in fixed.items():bp.validate_shape(value,schema['properties'][name])
    if key:
        for edit in head:bp.validate_shape(edit,schema['properties'][key]['items'])
    completed=fixed.get('article_edits',head if key=='article_edits' else [])
    ids=[e['id'] for e in completed]
    if len(ids)!=len(set(ids)) or not set(ids)<=set(packet['target_block_ids']):
        raise ContentQualityError('保存済みの完成編集に対象外・重複があります。')
    missing=[i for i in packet['target_block_ids'] if i not in ids]
    if key=='article_edits':
        if missing:
            remaining['properties'][key]['items']['properties']['id']['enum']=missing
        else:
            fixed[key]=head;remaining['properties'].pop(key);remaining['required'].remove(key);key=None
    # Keep original article and adopted reader focus, not the redundant full
    # blueprint/source dump. The suffix cannot change any completed paragraph.
    payload={'article_blocks':packet['article_blocks'],'completed_fields':fixed,
        'completed_edits_readonly':completed,'target_block_ids':missing,
        'mandatory_reader_block_ids':[i for i in packet['mandatory_reader_block_ids'] if i in missing],
        'paragraph_limits':{i:n for i,n in packet['paragraph_limits'].items() if i in missing},
        'scope_promises':packet['scope_promises'],'reader_focus':packet['article_design']['reader_focus'],
        'findings':packet['findings'],'missing_schema':remaining,
        'original_request_sha256':fingerprint,'partial_sha256':digest(artifact['content_text'])}
    result=copy.deepcopy(fixed)
    if remaining['properties']:
        system='保存済みの章内編集の不足部分だけを完成する。元の事実・条件・構成・読者設計を保つ。completed_edits_readonlyとcompleted_fieldsは変更・再出力禁止。target_block_idsの必要な編集とmissing_schemaの不足フィールドだけを返す。mandatory_reader_block_idsは全件を直し、paragraph_limits以内に収める。見出しの約束は実際の関連内容に合わせるだけにし、新しい話題・事実・免責・心理を足さない。JSONのみ。'
        tail,_=checked_request(job_id,'tiered_scoped_edit_suffix',dict(model=request['model'],max_tokens=10000,system=system,
            messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],output_config=bp.model_output_config(remaining),output_budget_includes_thinking=True),client=client)
        bp.validate_shape(tail,remaining)
        for name,value in tail.items():result[name]=copy.deepcopy(head)+value if name==key else value
    bp.validate_shape(result,schema)
    # This is explicit compositional recovery, never an invented raw response.
    from .db import upsert_artifact
    upsert_artifact(job_id=job_id,step='scoped_edit_suffix_recovery',content_type='application/json',
        content_text=json.dumps({'original_request_sha256':fingerprint,'partial_sha256':payload['partial_sha256'],
            'preserved_edit_ids':ids,'proposal':result},ensure_ascii=False),meta={'audited':False})
    return result
