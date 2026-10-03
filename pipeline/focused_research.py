"""Opt-in bounded rereading of saved sources; never grants coverage approval."""
import copy
import json
import os
from types import SimpleNamespace

VERSION = 'focused-research-v1'
READ_POLICY = '''
以前の不足理由は参考であり、原文に情報がないことの証明ではない。
今回の質問だけを保存済み資料から再読する。価格等は対象・期間・条件を含めて読む。
答えがなお不明なら未確認を維持する。資料一覧のタイトルやURLは証拠にしない。
'''
FOCUS_POLICY = '''
候補回答の全主張を原文で独立確認する。資料は引用先・確認済みURLを優先提示している。
source_catalogには本文を今回提示していない資料も含む。反証や別条件の確認に追加資料が
必要ならneeds_more_sources=trueを返す。その場合、今回の合格判定は採用されない。
sources_complete=trueは保存済み対象ページの集合を提示済みという意味。各本文はtruncatedなら抜粋であり、全文の提示やウェブ全体の探索完了ではない。
それでも不足なら未確認を返す。全体の網羅性は別工程で検査する。
'''


def enabled():
    return os.getenv('QUALITY_RESEARCH_ROUTING', '') == 'focused'


def related_pages(pages, candidates):
    urls = {u for i in candidates for u in i.get('official_checked_urls', [])}
    urls.update(r['url'] for i in candidates for r in i.get('evidence', []))
    selected = [p for p in pages if p['url'] in urls]
    # An unavailable official body is not a reason to hide saved secondary sources.
    return selected if any(p.get('text') and p.get('status', 'success') == 'success' for p in selected) else pages


def merge_visible_sources(original, reread):
    """Keep both views when a focused reread exposes previously clipped text."""
    result = copy.deepcopy(original)
    by_url = {p['url']:p for p in result}
    for page in reread:
        old = by_url.get(page['url'])
        if old is None:
            result.append(copy.deepcopy(page));by_url[page['url']]=result[-1]
        elif page.get('text', '') not in old.get('text', ''):
            # Preserve old neighboring conditions and newly visible evidence.
            old['text'] = (old.get('text', '')+'\n[中略：取得本文の抜粋]\n'+page.get('text', ''))
            old['truncated'] = bool(old.get('truncated') or page.get('truncated'))
    return result


def review_pending(job_id, index, value, questions, compact_plan, scope, selected, packed, searches):
    from .research_requirements import MATRIX_SCHEMA, MATRIX_SYSTEM, propose_optional_omissions
    from .source_spans import indexed_sources, span_schema, expand_references, SPAN_POLICY
    from .tiered_research import checked_request, needs_adjudication, packed_sources, validate_visible_matrix, POLICY
    from .content_quality import ContentQualityError, digest

    value = copy.deepcopy(value)
    inputs = outputs = 0
    planned = {q['id']:q for q in questions}
    catalog = [{k:p[k] for k in ('url', 'title', 'status', 'fetched_at', 'truncated', 'reason') if k in p} for p in selected]
    # Bind cached results to every available source, including omitted bodies.
    source_revision = digest(json.dumps(selected, ensure_ascii=False, sort_keys=True))

    def ask(candidates, pages, model, step, policy, expandable=False, complete=False):
        nonlocal inputs, outputs
        ids = {i['id'] for i in candidates}
        sources, source_index = indexed_sources(pages)
        schema = span_schema(MATRIX_SCHEMA)
        if expandable:
            root = schema['format']['schema']
            root['properties']['needs_more_sources'] = {'type':'boolean'}
            root['required'].append('needs_more_sources')
        payload = {'plan':{**scope, 'items':[q for q in questions if q['id'] in ids]},
                   'article_plan':{**compact_plan, 'items':[q for q in compact_plan['items'] if q['id'] not in ids]},
                   'candidate_answers':candidates, 'sources':sources, 'source_catalog':catalog,
                   'source_revision':source_revision, 'sources_complete':complete, 'searches':searches}
        request = dict(model=model, max_tokens=min(6000 if model == 'gpt-6-luna' else 10000, 2000+len(ids)*900),
                       system=MATRIX_SYSTEM+POLICY+SPAN_POLICY+policy+'\n回答対象はplan.itemsのみ。',
                       output_config=schema, messages=[{'role':'user','content':json.dumps(payload, ensure_ascii=False)}])
        result, usage = checked_request(job_id, f'tiered_focused_{index}_{step}', request)
        inputs += usage.input_tokens
        outputs += usage.output_tokens
        if expandable and type(result.get('needs_more_sources')) is not bool:
            raise ContentQualityError('資料追加の要否が不正です。')
        result = expand_references(result, source_index)
        validate_visible_matrix(result, payload['plan'], pages)
        return result

    def replace(result):
        replacements = {i['id']:i for i in result['items']}
        value['items'] = [replacements.get(i['id'], i) for i in value['items']]

    def require_enough_sources(result):
        if result['needs_more_sources']:
            for item in result['items']:
                item.update(status='unresearched', verified=False, basis='unresolved', answer='', evidence=[],
                            reason='保存済み資料の追加提示後も必要な根拠を確認できません。')
        return result

    # Optional omissions still require whole-article coverage approval.
    propose_optional_omissions(value['items'], {'items':questions}, packed)
    unknown = [i for i in value['items'] if not i.get('verified') and not i.get('omission_candidate_from')]
    if unknown:
        # One reread per subject. Use original saved bodies, not the earlier excerpt.
        selected_bodies = related_pages(selected, unknown)
        bodies = packed_sources(selected_bodies, unknown)
        complete = selected_bodies == selected
        result = ask(unknown, bodies, 'gpt-6-luna', 'reread', READ_POLICY+FOCUS_POLICY, True, complete)
        if result['needs_more_sources'] and not complete:
            bodies = packed_sources(selected, unknown)
            result = ask(unknown, bodies, 'gpt-6-luna', 'reread_expanded', READ_POLICY+FOCUS_POLICY, True, True)
        replace(require_enough_sources(result))
        propose_optional_omissions(value['items'], {'items':questions}, bodies)
        packed = merge_visible_sources(packed, bodies)

    pending = [i for i in value['items'] if i.get('verified') and i.get('basis') != 'omitted'
               and needs_adjudication(i, planned[i['id']])]
    if pending:
        # Preserve each already-visible page in full, including neighboring spans.
        focused = related_pages(packed, pending)
        complete = focused == packed
        result = ask(pending, focused, 'gpt-6.1-sol', 'audit', FOCUS_POLICY, True, complete)
        if result['needs_more_sources'] and not complete:
            # At most one expansion. No search, recursive escalation, or new budget.
            result = ask(pending, packed, 'gpt-6.1-sol', 'expanded', FOCUS_POLICY, True, True)
        replace(require_enough_sources(result))
    return value, SimpleNamespace(input_tokens=inputs, output_tokens=outputs)
