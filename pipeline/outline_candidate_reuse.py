"""Reuse a completed paid outline rejected by validation, never regenerate it.

The original exact provider-input snapshot and one settled end-turn receipt
must both exist. A mismatch is an inspection boundary, never permission to pay
for the same outline again. No claim, section or omission is invented here.
"""
import json
from types import SimpleNamespace

from .content_quality import ContentQualityError, digest


def saved(job_id, request):
    from .quality_budget import JOB, ledger
    if JOB.get()!=job_id:
        return None
    from .db import get_optional_artifact
    artifact=get_optional_artifact(job_id,'outline_design_incomplete')
    if not artifact:return None
    from .ai import prepare_provider_request
    request,_=prepare_provider_request(request)
    payload=json.dumps(request,ensure_ascii=False,sort_keys=True,
                       default=lambda block:block.model_dump(mode='json'))
    expected=json.loads(payload)
    record=get_optional_artifact(job_id,'authoring_request_'+digest(payload))
    if not record:
        raise ContentQualityError('保存構成の元の制作依頼を照合できません。構成APIは再送しません。')
    recorded=json.loads(record['content_text'])
    if recorded.get('stage')!='outline' or recorded.get('request')!=expected:
        raise ContentQualityError('保存構成と現在の制作依頼が一致しません。構成APIは再送しません。')
    with ledger() as value:
        calls=[c for c in value['calls'] if c.get('stage')=='outline' and c.get('provider')=='anthropic']
    if (len(calls)!=1 or calls[0].get('status')!='accounted'
            or calls[0].get('model')!=request['model']
            or calls[0].get('usage',{}).get('stop_reason')!='end_turn'):
        raise ContentQualityError('保存構成の完了応答・費用記録が一意に一致しません。構成APIは再送しません。')
    parsed=json.loads(artifact['content_text'])
    if not isinstance(parsed,dict) or not parsed.get('sections'):
        raise ContentQualityError('再利用できる完成節がありません。構成APIは再送しません。')
    usage=calls[0]['usage']
    return SimpleNamespace(content=[SimpleNamespace(type='text',text=artifact['content_text'])],
        usage=SimpleNamespace(input_tokens=usage['input_tokens'],output_tokens=usage['output_tokens']),
        stop_reason='end_turn',saved_candidate_sha256=digest(artifact['content_text']),
        saved_request_sha256=digest(payload))
