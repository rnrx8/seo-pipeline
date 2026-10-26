"""Versioned supplied product material; an understanding cache is not fact approval."""
import copy
import json
from .content_quality import ContentQualityError, digest

VERSION = 'production-brief-v1'
PRODUCT_VERSION = 'product-understanding-v1'


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def owned(value, tenant, label):
    if not value or not tenant or value.get('tenant_id') != tenant:
        raise ContentQualityError(label + 'の所有者を確認できません。')
    return value


def materials_for(service, sources):
    materials = []
    if service:
        points = service.get('selling_points') or []
        if isinstance(points, str): points = [points]
        for index, point in enumerate(points):
            if isinstance(point, str) and point.strip():
                materials.append({'id': f'service:point:{index}', 'text': point, 'origin': 'registered_service'})
        if service.get('raw_content'):
            materials.append({'id': 'service:raw', 'text': str(service['raw_content']), 'origin': 'registered_service'})
    for source in sources:
        if source.get('content_text'):
            text = str(source.get('content_text'))
            identifier = fingerprint([source.get('title'), text])[:20]
            materials.append({'id': 'source:' + identifier, 'title': source.get('title'), 'text': text, 'origin': 'registered_material'})
    return sorted({m['id']: m for m in materials}.values(), key=lambda m: m['id'])


def prepare(job_id, keyword, *, settings_snapshot=None):
    from .editorial_contract import CURRENT, validate
    active = CURRENT.get()
    if active:
        validate(active, job_id)
        if active['production_brief']['keyword'] != keyword:
            raise ContentQualityError('制作仕様のキーワードが一致しません。')
        return (copy.deepcopy(active['production_brief']), copy.deepcopy(active['product_input']),
                copy.deepcopy(active['cached_product']), 'frozen_assignment')
    from .db import (get_job, get_service_by_id, get_cta_by_id, get_primary_sources,
                     get_primary_sources_by_preset, get_company_settings, get_cached_product_context)
    job = get_job(job_id)
    tenant = job.get('tenant_id') or job.get('user_id')
    service = owned(get_service_by_id(job['service_id']), tenant, '商材') if job.get('service_id') else None
    cta = owned(get_cta_by_id(job['cta_id']), tenant, 'CTA') if job.get('cta_id') else None
    sources = get_primary_sources(tenant, job['category']) if tenant and job.get('category') else []
    if tenant and job.get('preset_id'):
        sources += get_primary_sources_by_preset(tenant, job['preset_id'])
    product_input = {
        'version': PRODUCT_VERSION, 'tenant_id': tenant,
        'service': {k: copy.deepcopy(service.get(k)) for k in ('id','name','url','category','must_include','must_exclude')} if service else None,
        'materials': materials_for(service, sources),
    }
    input_hash = fingerprint(product_input)
    cached = None
    cache_status = 'not_applicable'
    if service and job.get('tenant_id'):
        try:
            saved = get_cached_product_context(job['tenant_id'], input_hash)
            cached = json.loads(saved['content_text']) if saved else None
            if cached: validate_product(cached, product_input)
            cache_status = 'reused' if cached else 'miss'
        except Exception as exc:
            # Cache availability is not a reason to replay or block model stages.
            print('[product_context] Cache unavailable:', type(exc).__name__)
            cached = None
            cache_status = 'unavailable'
    # A new assignment must capture the actual catalog (which may contain
    # exclusions). A read outage is not an empty catalog. This precedes all
    # model calls; later stages reuse the complete saved assignment.
    companies = get_company_settings(tenant, job['category']) if tenant and job.get('category') else []
    if settings_snapshot is not None:
        settings_snapshot.update(service=copy.deepcopy(service), cta=copy.deepcopy(cta),
                                 companies=copy.deepcopy(companies), primary_sources=copy.deepcopy(sources))
    brief = {
        'version': VERSION, 'keyword': keyword, 'product_input_sha256': input_hash,
        'request': {k: copy.deepcopy(job.get(k)) for k in (
            'article_purpose','target_audience','custom_prompt','must_include','company_restriction',
            'must_reference_urls','never_reference_urls','tone_style','citation_style')},
        'product': product_input['service'],
        'cta': {k: copy.deepcopy(cta.get(k)) for k in ('name','body','button_text','url')} if cta else None,
        'registered_companies': [{k: copy.deepcopy(c.get(k)) for k in ('name','recommend_level','notes')} for c in companies],
    }
    return brief, product_input, cached, cache_status


def model_product_input(product_input):
    """Bound raw input, retaining the full source hash and explicit coverage."""
    result = copy.deepcopy(product_input)
    remaining = 24000
    for material in result['materials']:
        text = material['text']
        shown = text[:min(remaining, 12000)]
        material.update(text=shown, truncated=len(shown) != len(text), verification='supplied_not_verified')
        remaining -= len(shown)
    return result


def validate_product(value, product_input):
    if not isinstance(value, dict) or value.get('version') != PRODUCT_VERSION or value.get('input_sha256') != fingerprint(product_input):
        raise ContentQualityError('商品理解の資料版が一致しません。')
    if value.get('fact_status') != 'supplied_not_verified':
        raise ContentQualityError('登録情報を検証済み事実として扱えません。')
    expected = product_input.get('service')
    if value.get('service_id') != (expected or {}).get('id'):
        raise ContentQualityError('商品理解の対象が一致しません。')
    materials = {m['id']: m['text'] for m in product_input['materials']}
    for field in ('value_connections', 'maker_values'):
        rows = value.get(field)
        if not isinstance(rows, list): raise ContentQualityError('商品理解の形式が不正です。')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('source_refs'), list) or not row['source_refs']:
                raise ContentQualityError('商品の価値の根拠参照がありません。')
            for ref in row['source_refs']:
                if (not isinstance(ref, dict) or ref.get('id') not in materials
                    or not isinstance(ref.get('quote'), str) or not ref['quote'].strip()
                    or ref['quote'] not in materials[ref['id']]):
                    raise ContentQualityError('商品理解が登録資料の参照箇所と一致しません。')
            keys = ('situation','desired_change','value','constraints') if field == 'value_connections' else ('value',)
            if any(not isinstance(row.get(k), str) for k in keys) or not row['value'].strip():
                raise ContentQualityError('商品の価値と利用状況が不正です。')
    if not isinstance(value.get('unknowns'), list) or any(not isinstance(s,str) or not s.strip() for s in value['unknowns']):
        raise ContentQualityError('商品理解の不明点の形式が不正です。')
    return value


def product_record(understanding, product_input):
    value = {'version': PRODUCT_VERSION, 'input_sha256': fingerprint(product_input),
             'service_id': (product_input.get('service') or {}).get('id'),
             'fact_status': 'supplied_not_verified', **understanding}
    validate_product(value, product_input)
    value['input_coverage'] = [{'id': m['id'], 'truncated': m['truncated']} for m in model_product_input(product_input)['materials']]
    return value


def load(job_id):
    from .db import get_optional_artifact
    saved = get_optional_artifact(job_id, 'production_brief')
    if not saved: return None
    value = json.loads(saved['content_text'])
    if value.get('version') != VERSION:
        raise ContentQualityError('制作依頼の形式を確認できません。')
    return value
