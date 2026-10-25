"""Send the editorial blueprint without exceeding Claude's grammar compiler.

The original blueprint validator still enforces the complete schema. Both
generation and repair use this transport; non-blueprint requests are unchanged.
"""
import copy
import json


def prepare(request):
    from .editorial_blueprint import SCHEMA, model_output_config, repair_schema
    config = request.get('output_config') or {}
    schema = (config.get('format') or {}).get('schema')
    candidates = [SCHEMA]+[repair_schema(allow_lead=lead,allow_omissions=omissions,allow_titles=titles)
                          for lead in (False,True) for omissions in (False,True) for titles in (False,True)]
    original = next((value for value in candidates
                     if schema == value or schema == model_output_config(value)['format']['schema']), None)
    if original is None:
        return request, None
    wire = copy.deepcopy(request)
    wire['output_config'].pop('format')
    wire['system'] = wire.get('system', '') + (
        '\n【構成データの出力形式】以下のJSON Schemaに厳密に一致するJSONオブジェクトだけを返す。'
        'Markdownのコードフェンス、前置き、説明は付けない。必須項目、配列の件数、選択値、nullの扱いを守る。'
        '返却後に元のスキーマと参照関係を検証する。\n' + json.dumps(original, ensure_ascii=False))
    return wire, original


def validate(message, schema):
    if schema is None or getattr(message, 'stop_reason', None) != 'end_turn':
        return
    from .editorial_blueprint import validate_shape
    from .content_quality import ContentQualityError
    text = '\n'.join(b.text for b in message.content if getattr(b, 'type', 'text') == 'text' and hasattr(b, 'text'))
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        raise ContentQualityError('構成データのJSON形式が不正です。未検証の構成は採用しません。') from None
    validate_shape(value, schema)
