"""Recover complete values from an interrupted string-map response, once.

The original receipt and charge remain intact. Only a fully decoded value is
reused; truncated strings are never closed or published by the parser.
"""
import copy
import json
from .content_quality import ContentQualityError, digest


def completed_sections(text, schema):
    decoder = json.JSONDecoder()
    allowed = schema['properties']['sections']['properties']
    pos = 0
    def skip():
        nonlocal pos
        while pos < len(text) and text[pos].isspace(): pos += 1
    def token(value):
        nonlocal pos
        skip()
        if text[pos:pos+len(value)] != value:
            raise ContentQualityError('未完成の修正応答の区切りが不正です。')
        pos += len(value)
    token('{'); skip()
    key, pos = decoder.raw_decode(text, pos)
    if key != 'sections': raise ContentQualityError('未完成の修正応答の対象が不正です。')
    token(':'); token('{'); fixed = {}
    while True:
        skip()
        if pos >= len(text) or text[pos] == '}': break
        try: key, end = decoder.raw_decode(text, pos)
        except ValueError: break
        if key not in allowed or key in fixed:
            raise ContentQualityError('未完成の修正応答に対象外または重複した節があります。')
        pos = end; token(':'); skip()
        try: value, end = decoder.raw_decode(text, pos)
        except ValueError: break
        if not isinstance(value, str) or not value.strip():
            raise ContentQualityError('完成済みとして再利用する修正文が空または不正です。')
        fixed[key] = value; pos = end; skip()
        if pos >= len(text) or text[pos] == '}': break
        token(',')
    if not fixed:
        raise ContentQualityError('再利用できる完成済み修正文がありません。全体は再送しません。')
    return fixed


def recover(job_id, step, request, artifact, client):
    from .tiered_research import checked_request
    from .editorial_blueprint import validate_shape
    fingerprint = digest(json.dumps(request, ensure_ascii=False, sort_keys=True))
    if (artifact.get('meta', {}).get('request_sha256') != fingerprint
            or artifact['meta'].get('stop_reason') != 'max_tokens'):
        raise ContentQualityError('保存済み修正文と現在の修正依頼が一致しません。再送しません。')
    schema = request['output_config']['format']['schema']
    fixed = completed_sections(artifact['content_text'], schema)
    remaining = {k:copy.deepcopy(v) for k,v in schema['properties']['sections']['properties'].items() if k not in fixed}
    payload = json.loads(request['messages'][0]['content'])
    payload['target_section_ids'] = list(remaining)
    payload['target_sections'] = [s for s in payload.get('target_sections', []) if s['id'] in remaining]
    payload['completed_sections_readonly'] = fixed
    payload['original_request_sha256'] = fingerprint
    payload['partial_sha256'] = digest(artifact['content_text'])
    tail_schema = {'type':'object','properties':{'sections':{'type':'object','properties':remaining,
        'required':list(remaining),'additionalProperties':False}},'required':['sections'],'additionalProperties':False}
    suffix = {**request, 'max_tokens':min(32000, request['max_tokens']+8000),
        'system':request['system']+'\n保存済み応答の不足節だけを完成する。completed_sections_readonlyは完成済みで変更・再出力禁止。指定されたtarget_section_idsだけを返し、完成済み節との注釈・説明の対応を保つ。既に解決した指摘を再検討せず、不足節の実際の説明を完成する。',
        'messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
        'output_config':{'format':{'type':'json_schema','schema':tail_schema}}}
    if remaining:
        tail, usage = checked_request(job_id, step+'_suffix', suffix, client=client)
        validate_shape(tail, tail_schema)
    else:
        from types import SimpleNamespace
        tail={'sections':{}};usage=SimpleNamespace(input_tokens=0,output_tokens=0)
    value = {'sections':{**fixed, **tail['sections']}}
    validate_shape(value, schema)
    return value, usage, {'original_request_sha256':fingerprint,'partial_sha256':payload['partial_sha256'],
        'preserved_ids':list(fixed),'completed_ids':list(remaining)}
