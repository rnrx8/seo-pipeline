"""Resume exact paid writer responses, without treating a part as a final article.

Both the provider input and settled usage must agree. A stored body alone, a
prepared request alone, or a pending call is never permission to send it again.
Legacy part responses can be bound using the existing exact input and ledger.
"""
import json

from .content_quality import ContentQualityError, GenerationBusyError, digest


def saved(job_id, part, attempt, request):
    from .quality_budget import JOB, ledger
    if not job_id or JOB.get() != job_id:
        return None
    from .ai import prepare_provider_request
    from .db import get_optional_artifact, upsert_artifact
    wire, _ = prepare_provider_request(request)
    payload = json.dumps(wire, ensure_ascii=False, sort_keys=True,
                         default=lambda block: block.model_dump(mode='json'))
    key = digest(payload)
    step = f'article_part_{part}_response_{attempt}'
    response = get_optional_artifact(job_id, step)
    with ledger() as value:
        calls = [c for c in value['calls'] if c.get('provider_request_sha256') == key
                 and c.get('provider') == 'anthropic' and c.get('stage') == 'article']
    if any(c.get('status') in ('pending', 'reserved', 'unknown_cost_reserved') for c in calls):
        raise GenerationBusyError('同じ本文要求の実行・費用が未確定です。重複送信しません。')
    if not response and not calls:
        return None
    record = get_optional_artifact(job_id, 'authoring_request_' + key)
    snapshot = json.loads(record['content_text']) if record else {}
    meta = (response or {}).get('meta') or {}
    if (not response or not response['content_text'].strip()
            or snapshot.get('stage') != 'article' or snapshot.get('request') != json.loads(payload)
            or len(calls) != 1 or calls[0].get('status') != 'accounted'
            or calls[0].get('model') != wire['model']):
        raise ContentQualityError('保存済み本文と元の制作依頼・費用記録が一致しません。全体を再送しません。')
    usage = calls[0].get('usage') or {}
    if (meta.get('part') != part or meta.get('attempt') != attempt
            or (meta.get('provider_request_sha256') and meta['provider_request_sha256'] != key)
            or (meta.get('response_sha256') and meta['response_sha256'] != digest(response['content_text']))
            or meta.get('stop_reason') not in ('end_turn', 'max_tokens')
            or usage.get('stop_reason') != meta['stop_reason']
            or any(usage.get(k) != meta.get(k) for k in ('input_tokens', 'output_tokens'))):
        raise ContentQualityError('保存済み本文の応答・使用量の対応を確認できません。再送しません。')
    upsert_artifact(job_id=job_id, step=step + '_reuse', content_type='application/json',
        content_text=json.dumps({'request_sha256': key, 'response_sha256': digest(response['content_text']),
            'stop_reason': meta['stop_reason'], 'new_model_requests': 0, 'quality_accepted': False}, ensure_ascii=False))
    return response
