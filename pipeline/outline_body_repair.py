"""Reconcile a revised blueprint with saved prose, preserving unrelated sections.

Section IDs carry identity across moves and H4 splits. This is not a quality
acceptance path: normal whole-article QA and strict final checks still follow.
"""
import copy
import json
import re

from .content_quality import ContentQualityError
from .db import get_artifact, get_job, get_cta_by_id, upsert_artifact
from .editorial_blueprint import require_projection
from .section_identity import valid_binding

VERSION = 'outline-body-repair-v1'
HEADINGS = re.compile(r'^(#{2,4})[ \t]+(.+)$', re.M)


def fail(reason):
    raise ContentQualityError('構成変更後の範囲修正: ' + reason)


def sections(article, outline):
    """Split own prose, not subtrees; a parent move never consumes its children."""
    binding = (article.get('meta') or {}).get('section_map')
    text = article['content_text']
    if not valid_binding(text, outline['content_text'], binding):
        fail('元原稿の節対応が古いため、書き直す範囲を推測しません。')
    starts = list(HEADINGS.finditer(text))
    rows = {}
    for index, (match, entry) in enumerate(zip(starts, binding['entries'])):
        sid = entry.get('blueprint_id')
        if not sid or sid in rows:
            fail('元原稿に編集設計IDで対応できない見出しがあります。')
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        rows[sid] = {'id': sid, 'title': match[2], 'level': len(match[1]),
                     'start': match.start(), 'end': end,
                     'text': text[match.start():end],
                     'body': text[match.end():end].strip()}
    return text[:starts[0].start()] if starts else text, rows


def plan(article, before_outline, after_outline, report):
    from .content_edits import content_blocks
    old = require_projection(before_outline)
    new = require_projection(after_outline)
    if not old or not new:
        fail('編集設計のある原稿にだけ範囲修正を適用できます。')
    lead, rows = sections(article, before_outline)
    previous = {s['id']: s for s in old['sections']}
    current = {s['id']: s for s in new['sections']}
    if set(rows) != set(previous):
        fail('元の構成と本文で節IDが一致しません。')
    # Budgets alone do not authorize prose changes. Preserve all other semantic
    # differences, including parent/level changes and chapter outcome changes.
    semantic = lambda s: {k: v for k, v in s.items() if k != 'budget'}
    targets = {sid for sid, section in current.items()
               if sid not in previous or semantic(section) != semantic(previous[sid])}
    for sid, section in current.items():
        old_section = previous.get(sid)
        if old_section is None or old_section['parent_id'] != section['parent_id']:
            # Splitting old own-prose into new children must also remove that
            # explanation from the parent, even if its design text is unchanged.
            for parent in (section['parent_id'], (old_section or {}).get('parent_id')):
                if parent in current:
                    targets.add(parent)
    removed = set(previous) - set(current)
    for sid in removed:
        parent = previous[sid]['parent_id']
        while parent and parent not in current:
            parent = previous[parent]['parent_id']
        if parent:
            targets.add(parent)
    positions = sorted(rows.values(), key=lambda r: r['start'])
    addresses = {}; cursor = 0
    for block in content_blocks(article['content_text']):
        start = article['content_text'].index(block['text'], cursor)
        cursor = start + len(block['text'])
        start += len(block['text']) - len(block['text'].lstrip('\r\n'))
        owner = next((r['id'] for r in reversed(positions) if r['start'] <= start), None)
        addresses[block['id']] = owner
    for finding in report.get('editorial_findings', []):
        if finding.get('severity') != 'blocking':
            continue
        for bid in finding.get('block_ids', []):
            if bid not in addresses:
                fail('指摘の段落IDが元原稿に存在しません。')
            sid = addresses[bid]
            if sid in current:
                targets.add(sid)
    if not targets:
        fail('構成差分と指摘から修正対象を確認できません。')
    # Changed sections may refer to a neighbor by its old role/title. Permit
    # only adjacent existing peers, not every sibling or every child of an H2.
    peers = {}
    for s in new['sections']:
        peers.setdefault(s['parent_id'], []).append(s['id'])
    neighbors = set()
    for sid in targets:
        group = peers[current[sid]['parent_id']]
        i = group.index(sid)
        neighbors.update(group[j] for j in (i - 1, i + 1)
                         if 0 <= j < len(group) and group[j] in rows)
    writable = targets | neighbors
    return {'version': VERSION, 'lead': lead, 'source_sections': rows,
            'sections': new['sections'], 'target_section_ids': [s['id'] for s in new['sections'] if s['id'] in writable],
            'heading_change_ids': [sid for sid, s in current.items() if sid not in previous
                or (s['title'], s['level']) != (previous[sid]['title'], previous[sid]['level'])],
            'changed_section_ids': sorted(targets), 'neighbor_section_ids': sorted(neighbors - targets),
            'removed_section_ids': sorted(removed)}


def assemble(packet, replacements, outline):
    """The model cannot overwrite the lead or a read-only section."""
    wanted = set(packet['target_section_ids'])
    if not isinstance(replacements, dict) or set(replacements) != wanted:
        fail('修正文の節IDが許可範囲と一致しません。')
    pieces = [packet['lead']]
    for section in packet['sections']:
        sid = section['id']
        if sid in wanted:
            body = replacements[sid]
            if not isinstance(body, str) or not body.strip() or re.search(r'(?m)^\s*#{1,6}\s', body):
                fail('節の本文が空、または別の見出しを含んでいます。')
            title = (section['title'] if sid in packet['heading_change_ids']
                     else packet['source_sections'][sid]['title'])
            piece = '#' * section['level'] + ' ' + title + '\n\n' + body.strip()
        else:
            piece = packet['source_sections'][sid]['text'].rstrip('\n')
        pieces.append(piece + '\n\n')
    candidate = ''.join(pieces)
    if not candidate.startswith(packet['lead']):
        fail('対象外のリードが変更されています。')
    actual = list(HEADINGS.finditer(candidate))
    if [len(m[1]) for m in actual] != [s['level'] for s in packet['sections']]:
        fail('修正後の見出し階層が構成と一致しません。')
    entries = []; parent = None
    for i, (match, section) in enumerate(zip(actual, packet['sections'])):
        level = len(match[1])
        if level == 2:
            parent = match[2]
        entries.append({'id': f'section-{i:03d}', 'blueprint_id': section['id'], 'outline_index': i,
                        'level': level, 'title': match[2], 'parent': None if level == 2 else parent})
    # article_sections uses the immediately preceding H3 as the parent of H4.
    from .article_quality import article_sections
    for entry, actual_section in zip(entries, article_sections(candidate)):
        entry['parent'] = actual_section['parent']
    from .content_quality import digest
    binding = {'version': 1, 'outline_sha256': digest(outline), 'entries': entries}
    if not valid_binding(candidate, outline, binding):
        fail('修正後の節対応が不正です。')
    return candidate, binding


def run(job_id, keyword, api_key=None):
    from . import step_article
    before = get_artifact(job_id, 'outline_before_editorial_recovery')
    outline = get_artifact(job_id, 'outline')
    # Old non-blueprint jobs keep their existing route; do not guess identities.
    if not (before.get('meta', {}).get('blueprint_version') and outline.get('meta', {}).get('blueprint_version')):
        return step_article.run(job_id, keyword, api_key=api_key)
    original = get_artifact(job_id, 'article_before_editorial_recovery')
    if get_artifact(job_id, 'article')['content_text'] != original['content_text']:
        fail('構成修正の途中で元原稿が変わっています。保存応答を確認してください。')
    report = json.loads(get_artifact(job_id, 'content_audit_before_editorial_recovery')['content_text'])
    job = get_job(job_id)
    source = copy.deepcopy(original)
    cta_block = None
    if job.get('cta_id'):
        from .step_cta_inject import _format_cta_block
        cta = get_cta_by_id(job['cta_id'])
        if cta is None:
            fail('指定CTAを確認できません。')
        if any(cta.get(k) for k in ('body', 'button_text', 'url')):
            cta_block = _format_cta_block(cta)
        count = (original.get('meta', {}).get('cta_placement') or {}).get('count', 0)
        if (source['content_text'].count(cta_block) if cta_block else 0) != count:
            fail('保存CTAの本文と配置数が一致しません。推測で消去しません。')
        if cta_block:
            source['content_text'] = source['content_text'].replace(cta_block, '')
    packet = plan(source, before, outline, report)
    from .ai import get_step_config, is_openai_model, hybrid_writing_enabled
    from .generation_context import generation_evidence
    from .step_content_audit import WRITING_POLICY
    from .readability import READABILITY_POLICY, readability_issues
    from .evidence_policy import REVIEW_RESOLUTION_POLICY
    from .tiered_research import checked_request
    from .article_quality import validate_delivery
    from .step_structure_guard import validate_structure
    from .content_quality import digest
    contract = json.loads(get_artifact(job_id, 'content_contract')['content_text'])
    payload = {'source_sections': [{'id': r['id'], 'text': r['text']} for r in packet['source_sections'].values()],
               'target_section_ids': packet['target_section_ids'],
               'target_sections': [s for s in packet['sections'] if s['id'] in packet['target_section_ids']],
               'removed_section_ids': packet['removed_section_ids'],
               'editorial_findings': report.get('editorial_findings', []),
               'confirmed_facts': generation_evidence(job_id, '', ''), 'content_contract': contract}
    schema = {'type': 'object', 'properties': {'sections': {'type': 'object',
              'properties': {sid: {'type': 'string'} for sid in packet['target_section_ids']},
              'required': packet['target_section_ids'], 'additionalProperties': False}},
              'required': ['sections'], 'additionalProperties': False}
    model, limit = get_step_config('content_repair')
    request = dict(model=model, max_tokens=limit, system=WRITING_POLICY + '\n' + READABILITY_POLICY + '\n' + REVIEW_RESOLUTION_POLICY + '''
構成変更後の範囲修正を行う。source_sectionsは元原稿であり、完成例文ではない。
許可された節だけの本文をsectionsに返す。見出しとCTAはプログラムが扱うため本文に含めない。
節は子節を含まない自分自身の説明。新しいH4へ説明を移すときは、元の親節から同じ説明を除き、親の結論・子への自然な接続を残す。
移動・統合した節の具体的な答えと、確認済みの事実・対象・料金・利用条件を失わない。未確認事項を不存在・非公表へ変えない。
変更不要の節は元の本文を保つ。関係のない言い回し・共感を一新しない。
変更節の冒頭は具体的な結論とし、その章に合う読者の迷いや期待を受け止めて、次の判断へつなぐ。隣接節は必要な接続の修正だけに留める。
JSONのみを返す。''',
        messages=[{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
        output_config={'format': {'type': 'json_schema', 'schema': schema}})
    import anthropic
    client = None if is_openai_model(model) else anthropic.Anthropic(api_key=api_key, max_retries=0)
    if hybrid_writing_enabled():
        from .step_content_audit import repair_output_limit
        sizing = {'target_block_ids': packet['target_section_ids'], 'article_blocks': [
            {'id': sid, 'text': packet['source_sections'].get(sid, {}).get('body', '')}
            for sid in packet['target_section_ids']]}
        request['max_tokens'] = repair_output_limit(sizing, limit, lambda sample:
            client.with_options(max_retries=0).messages.count_tokens(model=model,
                messages=[{'role': 'user', 'content': sample}], timeout=30).input_tokens)
        request['output_budget_includes_thinking'] = True
    for attempt in range(2):
        value, _ = checked_request(job_id, f'tiered_content_repair_outline_sections_{attempt}', request, client=client)
        try:
            candidate, binding = assemble(packet, value['sections'], outline['content_text'])
            issues = validate_delivery(candidate, outline['content_text'], job.get('word_count_setting'), contract, binding)
            issues += readability_issues(candidate) + validate_structure(candidate, contract, outline=False)
            if cta_block and cta_block in candidate:
                issues.append({'key': 'cta_generated_in_body'})
            if issues:
                fail(json.dumps(issues, ensure_ascii=False))
            break
        except (ContentQualityError, KeyError) as exc:
            if attempt:
                raise
            request = {**request, 'messages': request['messages'] + [
                {'role': 'assistant', 'content': json.dumps(value, ensure_ascii=False)},
                {'role': 'user', 'content': '未適用です。同じ元原稿へ全対象を再提出してください。' + str(exc)}]}
    preserved = {sid: digest(row['text'].rstrip('\n')) for sid, row in packet['source_sections'].items()
                 if sid not in packet['target_section_ids'] and sid not in packet['removed_section_ids']}
    meta = {**original.get('meta', {}), 'section_map': binding, 'content_audited': False,
            'content_audit_snapshot': None, 'editorial_recovery_pending': True,
            'outline_body_repair': {'version': VERSION, 'source_sha256': digest(original['content_text']),
                'lead_sha256': digest(packet['lead']), 'target_section_ids': packet['target_section_ids'],
                'preserved_section_sha256': preserved, 'removed_section_ids': packet['removed_section_ids'],
                'lead_preserved': True, 'cta_reinserted_by_configured_step': bool(cta_block)}}
    from .article_design import correspondence, design_context
    meta['reader_correspondence'] = correspondence(candidate, outline['content_text'], design_context(outline), binding)
    # CTA positions are re-resolved against the new blueprint in the next step.
    meta.pop('cta_placement', None)
    return upsert_artifact(job_id=job_id, step='article', content_type='text/markdown',
                           content_text=candidate, meta=meta)
