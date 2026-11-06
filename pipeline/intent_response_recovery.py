"""Reuse a saved complete response after lossless transport/selection binding."""
import copy
import hashlib
import json
from .content_quality import ContentQualityError
from .production_brief import fingerprint
from .reader_needs import validate


def _same_sources(saved, current):
    """JSONB key ordering must not turn an identical request into a new call."""
    if not isinstance(saved, dict) or set(saved) != set(current):
        return False
    if any(saved[k] != current[k] for k in current if k != 'request'):
        return False
    try:
        return json.loads(saved['request']) == json.loads(current['request'])
    except (TypeError, ValueError, KeyError):
        return False


def recover(history, *, brief, sources, product_input, model):
    """Do not rewrite reader hypotheses or turn old analysis into verified facts."""
    if not history:
        return None
    attempts = history.get('attempts')
    if history.get('version') != 1 or not isinstance(attempts, list):
        raise ContentQualityError('保存済み検索意図応答の履歴形式を確認できません。')
    matching = []
    for attempt in attempts:
        try:
            candidate_payload = json.loads(attempt['request']['messages'][0]['content'])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if _same_sources(candidate_payload.get('input_sources'), sources):
            matching.append(attempt)
    if not matching:
        return None
    saved = matching[-1]
    request = saved.get('request')
    if not isinstance(request, dict) or fingerprint(request) != saved.get('request_sha256'):
        raise ContentQualityError('保存済み検索意図要求のハッシュが一致しません。再送しません。')
    try:
        payload = json.loads(request['messages'][0]['content'])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ContentQualityError('保存済み検索意図要求の入力を確認できません。') from exc
    if (fingerprint(payload.get('input_sources')) != saved.get('source_sha256')
            or not _same_sources(payload.get('input_sources'), sources)
            or payload.get('production_brief') != brief):
        raise ContentQualityError('保存済み検索意図応答と現在の制作依頼が一致しません。')
    if saved.get('model') != model or saved.get('stop_reason') != 'end_turn':
        raise ContentQualityError('保存済み検索意図応答は再利用できる完了応答ではありません。')
    raw = saved.get('raw_response')
    if not isinstance(raw, str):
        raise ContentQualityError('保存済み検索意図の実応答がありません。')
    value = json.loads(raw)
    original = copy.deepcopy(value)
    changes = []
    fit = value.get('product_fit')
    # Role describes a selected catalog service. Preserve the generated
    # conditional hypothesis/reason in the receipt and explicit request, but
    # do not pretend a service or its supporting material was selected.
    if (not brief.get('product') and isinstance(fit, dict)
            and fit.get('role') == 'conditional' and fit.get('material_ids') == []
            and fit.get('cta_role') in ('defer', 'omit')):
        fit['role'] = 'none'
        changes.append({'field': 'product_fit.role', 'before': 'conditional', 'after': 'none',
                        'reason': 'No selected production_brief.product; explicit request retained'})
    validate(value, sources, product_material_ids=[m['id'] for m in product_input['materials']],
             has_product=bool(brief['product']), has_cta=bool(brief['cta']), require_exploration=True)
    for field in ('selected', 'excluded', 'analysis'):
        if value[field] != original[field]:
            raise ContentQualityError('検索意図の再利用で文章や読者の問いが変わっています。')
    receipt = {
        'version': 'saved-intent-response-reuse-v1',
        'request_sha256': saved['request_sha256'],
        'raw_response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'source_sha256': fingerprint(sources), 'brief_sha256': fingerprint(brief),
        'saved_source_sha256': saved['source_sha256'],
        'request_serialization_only_changed': payload['input_sources']['request'] != sources['request'],
        'selection_binding': changes, 'original_product_fit': original.get('product_fit'),
        'transport_repairs': value.get('_transport_repairs', []),
        'analysis_wording_changed': False, 'new_model_requests': 0,
        'fact_verification': 'not_performed',
    }
    return value, receipt, {'model': model, 'input_tokens': saved.get('input_tokens'),
                            'output_tokens': saved.get('output_tokens')}
