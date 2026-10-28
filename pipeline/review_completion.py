"""Complete only missing structured review fields after a known output limit.

No incomplete verdict is accepted. Completed values, original inputs, receipt
and charges are retained; transport/credit/refusal failures never enter here.
"""
import copy
import json
from types import SimpleNamespace
from .content_quality import ContentQualityError, digest
from .db import get_optional_artifact, upsert_artifact
from .editorial_blueprint import validate_shape

POLICY = 'structured-review-v1'


def validate(value, schema):
    validate_shape(value,schema)
    if schema.get('type') == 'boolean' and type(value) is not bool:
        raise ContentQualityError('保存済み確認の真偽値が不正です。')
    if schema.get('type') == 'object':
        for key, child in schema['properties'].items():validate(value[key],child)
    elif schema.get('type') == 'array':
        for item in value:validate(item,schema['items'])


def fingerprint(request):
    return digest(json.dumps(request, ensure_ascii=False, sort_keys=True))


def preserve(job_id, step, request, response):
    parts = [p for item in response.get('output', []) if item.get('type') == 'message'
             for p in item.get('content', [])]
    if any(p.get('type') == 'refusal' for p in parts):
        raise ContentQualityError('確認モデルが確認を完了できませんでした。')
    text = '\n'.join(p['text'] for p in parts if p.get('type') == 'output_text')
    artifact = {'content_text':text, 'meta':{
        'completion_policy':POLICY, 'request_sha256':fingerprint(request),
        'partial_sha256':digest(text), 'model':request['model'],
        'stop_reason':'max_tokens', 'max_tokens':request['max_tokens'],
        'response_id':response.get('id'), 'usage':copy.deepcopy(response['usage'])}}
    prior = get_optional_artifact(job_id,step+'_incomplete')
    if prior and prior.get('meta',{}).get('request_sha256') != artifact['meta']['request_sha256']:
        # A deliberate material revision can start a new review. Preserve its
        # predecessor; an old partial must neither block it nor be transplanted.
        old_hash = prior.get('meta',{}).get('request_sha256')
        if not isinstance(old_hash,str) or len(old_hash)!=64:
            raise ContentQualityError('以前の未完了確認の識別情報を保全できません。')
        upsert_artifact(job_id=job_id,step=step+'_incomplete_'+old_hash,
            content_type='text/plain',content_text=prior['content_text'],meta=prior['meta'])
    upsert_artifact(job_id=job_id, step=step+'_incomplete', content_type='text/plain', **artifact)
    return artifact


def completed_fields(text, schema):
    """Decode complete leaves, including nested maps; never repair cut JSON."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:raise ContentQualityError('保存済み確認に重複項目があります。')
            value[key] = item
        return value
    decoder = json.JSONDecoder(object_pairs_hook=unique)
    pos = 0
    def skip():
        nonlocal pos
        while pos < len(text) and text[pos].isspace():pos += 1
    def obj(shape):
        nonlocal pos
        skip()
        if text[pos:pos+1] != '{':return {}
        pos += 1
        fixed = {}; seen = set()
        while True:
            skip()
            if pos >= len(text) or text[pos] == '}':break
            try:key, end = decoder.raw_decode(text, pos)
            except ValueError:break
            if not isinstance(key,str) or key not in shape['properties'] or key in seen:
                raise ContentQualityError('保存済み確認に対象外または重複した項目があります。')
            seen.add(key); pos = end; skip()
            if pos >= len(text):break
            if text[pos] != ':':raise ContentQualityError('保存済み確認の区切りが不正です。')
            pos += 1; skip(); child = shape['properties'][key]
            try:value, end = decoder.raw_decode(text, pos)
            except ValueError:
                # An array/string remains atomic. Nested object maps can retain
                # their already complete IDs without accepting an unfinished ID.
                if child.get('type') == 'object' and child.get('properties'):
                    nested = obj(child)
                    if nested:fixed[key] = nested
                break
            # A number at EOF may be the prefix of a longer number. A scalar is
            # reusable only after its delimiter is present.
            if end == len(text) and not isinstance(value,(dict,list,str)):break
            validate(value,child)
            fixed[key] = value; pos = end; skip()
            if pos >= len(text) or text[pos] == '}':break
            if text[pos] != ',':raise ContentQualityError('保存済み確認の区切りが不正です。')
            pos += 1
        return fixed
    if not text.strip():return {}
    if schema.get('type') != 'object' or not schema.get('properties'):
        raise ContentQualityError('この確認形式の不足補完には対応していません。')
    skip()
    if text[pos:pos+1] != '{':raise ContentQualityError('保存済み確認の形式が不正です。')
    return obj(schema)


def remaining_schema(schema, fixed):
    result = copy.deepcopy(schema); properties = {}
    for key, child in schema['properties'].items():
        if key not in fixed:properties[key] = copy.deepcopy(child)
        elif child.get('type') == 'object':
            tail = remaining_schema(child,fixed[key])
            if tail['properties']:properties[key] = tail
    result.update(properties=properties, required=list(properties))
    return result


def merge_fixed(fixed, tail):
    result = copy.deepcopy(fixed)
    for key, value in tail.items():
        if key not in result:result[key] = value
        elif isinstance(value,dict) and isinstance(result[key],dict):
            result[key] = merge_fixed(result[key],value)
        else:raise ContentQualityError('不足補完が完成済み確認を変更しています。')
    return result


def recover_and_save(job_id, step, request, artifact, client, round_number):
    from .tiered_research import checked_request
    meta = artifact.get('meta',{})
    if (meta.get('completion_policy') != POLICY or meta.get('request_sha256') != fingerprint(request)
            or meta.get('partial_sha256') != digest(artifact['content_text'])
            or meta.get('stop_reason') != 'max_tokens'):
        raise ContentQualityError('保存済み確認と現在の資料が一致しません。再送しません。')
    schema = request['output_config']['format']['schema']
    fixed = completed_fields(artifact['content_text'],schema)
    remaining = remaining_schema(schema,fixed)
    usage = SimpleNamespace(input_tokens=0,output_tokens=0)
    if remaining['properties']:
        if round_number >= 2:
            raise ContentQualityError('確認結果の不足補完が進みません。保存済み結果を保持しています。')
        # Keep all messages/settings and source evidence. Completed verdicts are
        # immutable context. Only the output schema is narrowed to missing data.
        suffix = copy.deepcopy(request)
        suffix['max_tokens'] = min(32000,max(16000,request['max_tokens']*2))
        suffix['system'] += ('\n出力上限で途切れた確認結果の不足項目だけを完成する。'
            'completed_review_readonlyは完成済みで変更・再出力禁止。元の資料・判定基準を保ち、'
            '指定された不足項目だけをschemaどおり返す。未完成だから合格にすることは禁止。')
        suffix['messages'].append({'role':'user','content':json.dumps({
            'completed_review_readonly':fixed,'original_request_sha256':fingerprint(request),
            'partial_sha256':meta['partial_sha256']},ensure_ascii=False)})
        suffix['output_config']['format']['schema'] = remaining
        tail, usage = checked_request(job_id,step+'_completion',suffix,
                                      client=client,_completion_round=round_number+1)
        validate(tail,remaining)
        value = merge_fixed(fixed,tail)
    else:value = fixed
    validate(value,schema)
    text = json.dumps(value,ensure_ascii=False)
    previous = get_optional_artifact(job_id,step)
    saved_meta = copy.deepcopy((previous or {}).get('meta',{}))
    if previous and saved_meta.get('request_sha256') and saved_meta.get('result_sha256') == digest(previous['content_text']):
        saved_meta.setdefault('request_history',{})[saved_meta['request_sha256']] = {
            'content_text':previous['content_text'],'result_sha256':saved_meta['result_sha256']}
    saved_meta.update(request_sha256=fingerprint(request),result_sha256=digest(text),
        model=request['model'],input_tokens=usage.input_tokens,output_tokens=usage.output_tokens,
        structured_completion={'policy':POLICY,'partial_sha256':meta['partial_sha256'],
            'preserved_fields':list(fixed),'completed_fields':list(remaining['properties'])})
    upsert_artifact(job_id=job_id,step=step,content_type='application/json',content_text=text,
                    meta=saved_meta)
    original = meta.get('usage',{})
    return value,SimpleNamespace(input_tokens=usage.input_tokens+original.get('input_tokens',0),
                                output_tokens=usage.output_tokens+original.get('output_tokens',0))
