"""Complete only the missing suffix of a source-bound structured blueprint."""
import copy
import json

from .content_quality import ContentQualityError, digest
from .db import get_optional_artifact

POLICY = '''保存済み編集設計の未完成部分だけを補完する。
completed_fieldsとarray_prefixの完成要素は変更・再出力しない。
missing_schemaにある残りのフィールドだけをJSONで返す。未完成配列は既存要素の続きだけを返す。
sectionsが対象なら既存の章・ID・親子・次の疑問への接続を引き継ぎ、必要な残りの節を補う。
省略理由は確認済みの調査範囲に従う。完了済み設計、調査の重要度、根拠の条件は変更しない。
discarded_fragmentは途中で切れた参考情報であり、完成済み設計や根拠ではない。
不足フィールドが空配列で成立するなら空配列でよい。JSON以外は出力しない。
'''


def fingerprint(request):
    return digest(json.dumps(request, ensure_ascii=False, sort_keys=True))


def prefix(text, schema, *, preserved_array='sections', require_sections=True):
    """Keep only fully decoded values; never close strings or invent JSON values."""
    decoder = json.JSONDecoder()
    fields = {}
    array_key = None
    array_prefix = []
    def skip(i):
        while i < len(text) and text[i].isspace(): i += 1
        return i
    pos = skip(0)
    if pos >= len(text) or text[pos] != '{':
        raise ContentQualityError('保存構成の先頭がJSONオブジェクトではありません。')
    pos = skip(pos+1)
    while pos < len(text) and text[pos] != '}':
        try: key, end = decoder.raw_decode(text, pos)
        except ValueError: break
        if not isinstance(key, str) or key not in schema['properties'] or key in fields:
            raise ContentQualityError('保存構成のフィールドが不正・重複しています。')
        pos = skip(end)
        if pos >= len(text): break
        if text[pos] != ':': raise ContentQualityError('保存構成の区切りが不正です。')
        pos = skip(pos+1)
        start = pos
        try: value, end = decoder.raw_decode(text, pos)
        except ValueError:
            if pos < len(text) and text[pos] == '[' and schema['properties'][key].get('type') == 'array':
                array_key = key; pos = skip(pos+1)
                while pos < len(text):
                    try: value, end = decoder.raw_decode(text, pos)
                    except ValueError: break
                    array_prefix.append(value); pos = skip(end)
                    if pos >= len(text) or text[pos] == ']': break
                    if text[pos] != ',': raise ContentQualityError('保存構成の配列区切りが不正です。')
                    pos = skip(pos+1)
            pos = start
            break
        fields[key] = value; pos = skip(end)
        if pos >= len(text) or text[pos] == '}': break
        if text[pos] != ',': raise ContentQualityError('保存構成のフィールド区切りが不正です。')
        pos = skip(pos+1)
    # A useful completed section is required to avoid disguising a full rewrite.
    sections = fields.get(preserved_array, array_prefix if array_key == preserved_array else [])
    if require_sections and not sections:
        raise ContentQualityError('再利用できる完成節がありません。全文再生成は行いません。')
    missing = {k:copy.deepcopy(v) for k,v in schema['properties'].items() if k not in fields}
    if array_key:
        missing[array_key]['minItems'] = 0
        if 'maxItems' in missing[array_key]:
            missing[array_key]['maxItems'] -= len(array_prefix)
    return fields, array_key, array_prefix, {'type':'object','properties':missing,'required':list(missing),'additionalProperties':False}, text[pos:][-2000:]


def advance(fixed, key, head, remaining, text):
    """Merge only complete suffix values; progress must be strictly monotonic."""
    from .editorial_blueprint import validate_shape
    fields, next_key, next_head, rest, fragment = prefix(text, remaining, require_sections=False)
    for name,value in fields.items(): validate_shape(value,remaining['properties'][name])
    if next_key:
        for row in next_head: validate_shape(row,remaining['properties'][next_key]['items'])
    if not fields and not next_head:
        raise ContentQualityError('構成の補完応答に再利用できる完成要素がありません。同じ要求は再送しません。')
    result=copy.deepcopy(fixed)
    for name,value in fields.items():
        result[name]=copy.deepcopy(head)+value if name==key else copy.deepcopy(value)
    if next_key==key: next_head=copy.deepcopy(head)+next_head
    sections=result.get('sections',next_head if next_key=='sections' else [])
    ids=[s['id'] for s in sections]
    if len(ids)!=len(set(ids)):
        raise ContentQualityError('構成の補完が完成済みの節IDを再出力しています。上書き・再送しません。')
    return result,next_key,next_head,rest,fragment


def complete(job_id, artifact, request, context, client):
    from . import editorial_blueprint as bp
    from .tiered_research import checked_request, cached_result
    from .editorial_contract import enrich_request
    from types import SimpleNamespace
    if (artifact.get('meta',{}).get('request_sha256') != fingerprint(request)
            or artifact['meta'].get('stop_reason') != 'max_tokens'):
        raise ContentQualityError('未完成構成と現在の制作依頼が一致しません。再送しません。')
    expected_schema = bp.schema()
    fixed, key, head, remaining, fragment = prefix(artifact['content_text'], expected_schema)
    bp.validate_shape(fixed, {'type':'object','properties':{k:expected_schema['properties'][k] for k in fixed},'required':list(fixed),'additionalProperties':False})
    if key:
        for row in head: bp.validate_shape(row, expected_schema['properties'][key]['items'])
    inputs=outputs=0
    chunk=1
    while remaining['properties']:
        payload = {'completed_fields':fixed,'array_prefix':{key:head} if key else {},
                   'missing_schema':remaining,'discarded_fragment':fragment,'production_context':context,
                   'original_request_sha256':fingerprint(request),'partial_sha256':digest(artifact['content_text'])}
        completion_request = dict(model=request['model'],max_tokens=8000 if chunk==1 else max(8000,min(16000,request['max_tokens'])),system=POLICY,
            output_config=bp.model_output_config(remaining),
            messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
        completion_request=enrich_request(completion_request)
        step='outline_suffix_completion'+(f'_{chunk}' if chunk>1 else '')
        tail=cached_result(job_id,step,completion_request)
        partial=get_optional_artifact(job_id,step+'_incomplete') if tail is None else None
        if tail is None and partial is None:
            try:
                tail,usage=checked_request(job_id,step,completion_request,client=client)
            except ContentQualityError:
                partial=get_optional_artifact(job_id,step+'_incomplete')
                if not partial:raise
            else:
                inputs+=usage.input_tokens;outputs+=usage.output_tokens
        if partial is not None:
            meta=partial.get('meta',{})
            if meta.get('request_sha256')!=fingerprint(completion_request) or meta.get('stop_reason')!='max_tokens':
                raise ContentQualityError('保存済みの構成補完と現在の不足範囲が一致しません。再送しません。')
            fixed,key,head,remaining,fragment=advance(fixed,key,head,remaining,partial['content_text'])
            inputs+=meta.get('input_tokens',0);outputs+=meta.get('output_tokens',0)
            chunk+=1
            continue
        bp.validate_shape(tail, remaining)
        break
    else: tail={}
    result = copy.deepcopy(fixed)
    for name,value in tail.items():
        result[name] = copy.deepcopy(head)+value if name==key else value
    bp.validate_shape(result,expected_schema)
    return result, SimpleNamespace(input_tokens=inputs,output_tokens=outputs)


def saved(job_id, request):
    artifact = get_optional_artifact(job_id,'outline_incomplete')
    if artifact and not artifact.get('meta',{}).get('request_sha256'):
        binding=get_optional_artifact(job_id,'outline_incomplete_binding')
        value=json.loads(binding['content_text']) if binding else {}
        if (value.get('request_sha256')==fingerprint(request)
                and value.get('partial_sha256')==digest(artifact['content_text'])):
            artifact=copy.deepcopy(artifact)
            artifact['meta']['request_sha256']=value['request_sha256']
    if artifact and artifact.get('meta',{}).get('request_sha256') != fingerprint(request):
        raise ContentQualityError('未完成構成の制作依頼との対応が未確認です。全体を再送しません。')
    return artifact
