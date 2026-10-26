import json
import re
from datetime import datetime, timezone
import anthropic
from .ai import create_with_retry, get_step_config, message_text
from .db import get_artifact, get_optional_artifact, upsert_artifact
from .production_brief import prepare, model_product_input, product_record, fingerprint
from .reader_needs import POLICY, output_schema, validate, record, VERSION as NEEDS_VERSION
from .intent_exploration import VERSION as METHOD_VERSION

MODEL, MAX_TOKENS = get_step_config("search_intent")

# ─── 起点ヒント（query_attrs）─────────────────────────────────────────────────
# SERPから軽量にクエリ属性を生成する。用途は2つ:
#   (a) 記事詳細UIの「クエリ属性分析」カード（QueryAttrsCard）の表示
#   (b) 読者理解の補助ヒント。属性は推定であり観測根拠にはしない。
# 画面互換のスキーマを保ち、読者理解は主分析からreader_needsへ保存する。
QUERY_ATTRS_PROMPT = """\
以下の検索結果データから、このキーワードのクエリ属性を分析してJSON形式のみで返してください。
年齢・性別は資料から説明できない場合は不明にする。検索者全員の実態や統計と扱わず、推定の理由・限界をnotesに残す。

キーワード: {keyword}

検索結果:
{serp_text}

以下のJSON形式のみを返してください（説明文不要）：
{{
  "gender_tendency": "男性多め|女性多め|混在（均等）|不明",
  "age_range": "推定年齢層（例：20〜30代中心、40代以上）",
  "content_types": ["まとめ・比較記事", "体験談・口コミ", "公式サイト・LP", "専門家コラム", "動画コンテンツ"],
  "searcher_stage": "情報収集|比較検討|購入・申込直前|複数混在",
  "key_concerns": ["読者が最も気にしていること1", "気にしていること2", "気にしていること3"],
  "competition_level": "高|中|低",
  "notes": "その他の特記事項（任意、なければnull）"
}}
"""

SYSTEM_PROMPT = POLICY


def _save_failed_analysis(job_id, message, sources, request, error):
    """Retain the received response without accepting it or retrying a paid call."""
    previous = get_optional_artifact(job_id, 'search_intent_failures')
    history = json.loads(previous['content_text']) if previous else {'version': 1, 'attempts': []}
    if history.get('version') != 1 or not isinstance(history.get('attempts'), list):
        raise ValueError('Invalid saved search-intent failure history')
    history['attempts'].append({
        'at': datetime.now(timezone.utc).isoformat(), 'model': MODEL,
        'source_sha256': fingerprint(sources), 'request_sha256': fingerprint(request),
        'request': request,
        'raw_response': message_text(message), 'stop_reason': getattr(message, 'stop_reason', None),
        'input_tokens': getattr(getattr(message, 'usage', None), 'input_tokens', None),
        'output_tokens': getattr(getattr(message, 'usage', None), 'output_tokens', None),
        'error_type': type(error).__name__, 'validation_error': str(error),
    })
    upsert_artifact(job_id=job_id, step='search_intent_failures', content_type='application/json',
                    content_text=json.dumps(history, ensure_ascii=False),
                    meta={'diagnostic_only': True, 'completed_analysis': False})


def _generate_query_attrs(client: anthropic.Anthropic, job_id: str, keyword: str, serp_text: str) -> dict:
    """SERPからクエリ属性を生成して保存。起点ヒント(dict)を返す。失敗時は空dict。"""
    try:
        print("[search_intent] Generating query attributes (hint)...")
        model, max_tokens = get_step_config("query_attrs")
        attrs_msg = create_with_retry(
            client,
            model=model,
            max_tokens=max_tokens,
            system="SEOの専門家として、検索クエリの属性をJSONで分析してください。",
            messages=[{
                "role": "user",
                "content": QUERY_ATTRS_PROMPT.format(keyword=keyword, serp_text=serp_text[:3000]),
            }],
        )
        attrs_text = message_text(attrs_msg).strip()
        match = re.search(r"\{[\s\S]*\}", attrs_text)
        if not match:
            return {}
        attrs_json = json.loads(match.group(0))
        if not isinstance(attrs_json, dict): return {}
        upsert_artifact(
            job_id=job_id,
            step="query_attrs",
            content_type="application/json",
            content_text=json.dumps(attrs_json, ensure_ascii=False),
            meta={'attribute_status':'estimate_not_verified'},
        )
        print("[search_intent] Query attributes saved.")
        return attrs_json
    except Exception as e:
        print(f"[search_intent] Warning: could not generate query attributes: {e}")
        return {}


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Analyze reader value and supplied product material in the existing Opus call."""
    serp = get_artifact(job_id, 'serp')
    serp_text = serp['content_text']
    brief, product_input, cached, cache_status = prepare(job_id, keyword)
    client = anthropic.Anthropic(api_key=api_key)
    from .editorial_contract import CURRENT
    assignment_meta = {'editorial_contract': CURRENT.get()} if CURRENT.get() else {}
    upsert_artifact(job_id=job_id, step='production_brief', content_type='application/json',
                    content_text=json.dumps(brief, ensure_ascii=False), meta=assignment_meta)
    # Invalidate old derived reader design even if this model call stops early.
    upsert_artifact(job_id=job_id, step='reader_needs', content_type='application/json',
                    content_text=json.dumps({'version':NEEDS_VERSION, 'status':'analyzing'}))
    upsert_artifact(job_id=job_id, step='intent_chains', content_type='application/json',
                    content_text=json.dumps({'chains':[], 'superseded_by':'reader_needs'}))
    attrs = _generate_query_attrs(client, job_id, keyword, serp_text)
    sources = {'keyword':keyword, 'serp':serp_text,
               'request':json.dumps(brief['request'], ensure_ascii=False)}
    new_product = bool(brief['product'] and not cached)
    payload = {'input_sources':sources, 'production_brief':brief,
               'query_hints':attrs, 'product_context':cached,
               'product_materials':model_product_input(product_input) if new_product else None}
    request = dict(model=MODEL, max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT + '\nselectedのidはi01等。採用した問いを調査・構成へ引き継ぐ。商品理解が既存の場合は資料全文を再分析せず、その不明点と条件を維持する。商品理解のvalue_connectionsはこのキーワードだけの訴求へ限定せず、資料から説明できる利用状況と価値を整理する。',
        output_config=output_schema(new_product=new_product),
        messages=[{'role':'user','content':json.dumps(payload, ensure_ascii=False)}])
    from .editorial_contract import enrich_request
    request = enrich_request(request)
    message = create_with_retry(client, **request)
    try:
        if getattr(message, 'stop_reason', 'end_turn') != 'end_turn' or not message_text(message).strip():
            from .content_quality import ContentQualityError
            raise ContentQualityError('検索意図分析が正常終了していません。古い読者理解を流用しません。')
        value = json.loads(message_text(message))
        validate(value, sources, product_material_ids=[m['id'] for m in product_input['materials']],
                 has_product=bool(brief['product']), has_cta=bool(brief['cta']), require_exploration=True)
    except Exception as error:
        try:
            _save_failed_analysis(job_id, message, sources, request, error)
        except Exception as save_error:
            from .content_quality import ContentQualityError
            raise ContentQualityError('不合格の検索意図応答を保存できませんでした: ' + str(error)) from save_error
        raise
    if new_product:
        product = product_record(value['product_understanding'], product_input)
    else:
        product = cached or product_record({'value_connections':[], 'maker_values':[], 'unknowns':[]}, product_input)
    upsert_artifact(job_id=job_id, step='product_context', content_type='application/json',
        content_text=json.dumps(product, ensure_ascii=False),
        meta={'input_sha256':product['input_sha256'], 'cache_status':cache_status, 'fact_status':'supplied_not_verified'})
    needs = record(value, brief=brief, intent_sources=sources)
    upsert_artifact(job_id=job_id, step='reader_needs', content_type='application/json',
        content_text=json.dumps(needs, ensure_ascii=False), meta={'version':NEEDS_VERSION, 'analysis_method_version':METHOD_VERSION,
              'analysis_method_sha256':needs['analysis_method_sha256']})
    return upsert_artifact(job_id=job_id, step='search_intent', content_type='text/markdown',
        content_text=value['analysis'], meta={'model':MODEL, 'reader_needs_version':NEEDS_VERSION,
        'analysis_method_version':METHOD_VERSION, 'analysis_method_sha256':needs['analysis_method_sha256'],
        'input_tokens':message.usage.input_tokens, 'output_tokens':message.usage.output_tokens})
