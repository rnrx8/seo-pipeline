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


def finish_failed_lead(client, candidate, outline, keyword, job, report):
    """Preserve accepted/unflagged leads; fix an already failed lead before QA."""
    findings = [f for f in report.get('editorial_findings', [])
                if f.get('severity') == 'blocking' and f.get('origin') == 'individual_lead_review']
    if not findings: return candidate, None
    from . import step_article
    from .db import get_learned_style_rules
    rules = get_learned_style_rules(job['tenant_id']) if job.get('tenant_id') else []
    feedback = '\n'.join(f['problem']+' / '+f['required_change'] for f in findings)
    revised, _, _, meta = step_article._finish_reader_lead(
        client, candidate, outline, keyword, job, rules, review_feedback=feedback)
    if revised[HEADINGS.search(revised).start():] != candidate[HEADINGS.search(candidate).start():]:
        fail('リード修正で対象外の本文が変更されています。')
    return revised, meta


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
    if not targets and not removed:
        fail('構成差分と指摘から修正対象を確認できません。')
    # Neighbors are context, never implicit write permission. Changing their
    # prose without a finding was itself reintroducing already removed claims.
    peers = {}
    for s in new['sections']:
        peers.setdefault(s['parent_id'], []).append(s['id'])
    neighbors = set()
    for sid in targets:
        group = peers[current[sid]['parent_id']]
        i = group.index(sid)
        neighbors.update(group[j] for j in (i - 1, i + 1)
                         if 0 <= j < len(group) and group[j] in rows)
    writable = targets
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
    # Use the same parent semantics as native delivery checks and QA binding.
    from .article_quality import article_sections
    entries = [{'id': f'section-{i:03d}', 'blueprint_id': section['id'], 'outline_index': i,
                **{k: actual_section[k] for k in ('level', 'title', 'parent')}}
               for i, (actual_section, section) in enumerate(zip(article_sections(candidate), packet['sections']))]
    from .content_quality import digest
    binding = {'version': 1, 'outline_sha256': digest(outline), 'entries': entries}
    if not valid_binding(candidate, outline, binding):
        fail('修正後の節対応が不正です。')
    return candidate, binding


def scoped_retry(request, packet, candidate, binding, outline, issues):
    """A mechanical defect narrows the next request; valid sections stay fixed."""
    from .content_edits import content_blocks
    _, rows = sections({'content_text':candidate, 'meta':{'section_map':binding}}, outline)
    owners = {}; cursor = 0
    for block in content_blocks(candidate):
        start = candidate.index(block['text'], cursor)
        cursor = start + len(block['text'])
        start += len(block['text']) - len(block['text'].lstrip())
        owners[block['id']] = next((sid for sid, r in rows.items() if r['start'] <= start < r['end']), None)
    targets = set()
    for issue in issues:
        located = {owners.get(row['id']) for row in issue.get('affected_blocks', [])}
        located.discard(None)
        if not located:
            located = {sid for sid, row in rows.items() if row['title'] == issue.get('heading')}
        if not located or not located <= set(packet['target_section_ids']):
            fail('検査指摘を変更可能な節へ特定できません。全対象の再送はしません。')
        targets.update(located)
    ids = [s['id'] for s in packet['sections'] if s['id'] in targets]
    source = json.loads(request['messages'][0]['content'])
    payload = {'source_sections':[{'id':sid,'text':rows[sid]['text']} for sid in ids],
        'target_section_ids':ids, 'target_sections':[s for s in packet['sections'] if s['id'] in targets],
        'structural_issues':issues, 'content_contract':source['content_contract']}
    if source.get('citation_registry'):
        payload['citation_registry'] = source['citation_registry']
    if any(issue['key'] != 'continuous_prose' for issue in issues):
        payload['confirmed_facts'] = source['confirmed_facts']
    return {**request, 'max_tokens':min(request['max_tokens'],6000), 'output_budget_includes_thinking':False,
        'system':request['system']+'\n今回は機械検査に残った対象節だけを修正する。既存原稿の事実・数値・対象条件と必要な感情理解は保持し、新情報を追加しない。target_section_ids以外は返さない。',
        'messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
        'output_config':{'format':{'type':'json_schema','schema':{'type':'object','properties':{
            'sections':{'type':'object','properties':{sid:{'type':'string'} for sid in ids},
                        'required':ids,'additionalProperties':False}},'required':['sections'],'additionalProperties':False}}}}


def candidate_signature(packet, outline, payload, system, model):
    from .content_quality import digest
    return digest(json.dumps([packet,outline['content_text'],payload,system,model],ensure_ascii=False,sort_keys=True))


def cached_candidate(job_id, signature, outline, contract, setting):
    """A completed body survives a lead/provider failure; never inherits QA."""
    try:
        saved = get_artifact(job_id, 'outline_body_repair_candidate')
    except (ValueError,KeyError):
        return None
    meta = saved.get('meta') or {}
    if meta.get('input_sha256') != signature:
        return None
    from .content_quality import digest
    from .article_quality import validate_delivery
    from .readability import readability_issues
    from .step_structure_guard import validate_structure
    text = saved['content_text'];binding = meta.get('section_map')
    if meta.get('text_sha256') != digest(text):
        fail('保存した修正候補の本文が変わっています。再生成前に保存状態を確認してください。')
    issues = validate_delivery(text,outline['content_text'],setting,contract,binding)
    issues += readability_issues(text) + validate_structure(text,contract,outline=False)
    if meta.get('citation_registry'):
        from .article_citations import integrity_issues
        issues += integrity_issues(text,meta['citation_registry'])
    if issues:
        fail('保存候補の検査が未合格です。全文を再送しません。'+json.dumps(issues,ensure_ascii=False))
    return text,binding


def preserved_sections(packet,candidate,binding,outline,citation_registry=None):
    """Record source rebinding separately; it never permits prose changes."""
    from .content_quality import digest
    _,current = sections({'content_text':candidate,'meta':{'section_map':binding}},outline)
    kept = {};rebound = []
    def prose(value):
        from .article_citations import DEFINITION
        value='\n'.join(line for line in value.splitlines() if not DEFINITION.fullmatch(line.strip()))
        return re.sub(r'(?:[ \t]*※\d+)+','',value).strip()
    for sid,row in packet['source_sections'].items():
        if sid in packet['target_section_ids'] or sid in packet['removed_section_ids']:continue
        old=row['text'].rstrip('\n');new=current[sid]['text'].rstrip('\n')
        if old == new:
            kept[sid]=digest(old)
        elif citation_registry is not None and prose(old) == prose(new):
            rebound.append(sid)
        else:
            fail('変更対象外の本文が変わっています: '+sid)
    return kept,rebound


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
    citation_registry = None
    if job.get('citation_style') == 'inline_footnote':
        from .article_citations import from_context, for_article, POLICY as CITATION_POLICY
        registry_hint = original.get('meta',{}).get('citation_registry')
        if registry_hint is None:
            try:
                staged = get_artifact(job_id,'outline_body_repair_candidate')
            except (ValueError,KeyError):
                staged = None
            staged_meta = (staged or {}).get('meta') or {}
            if (staged_meta.get('source_sha256') == digest(original['content_text'])
                    and staged_meta.get('outline_sha256') == digest(outline['content_text'])
                    and staged_meta.get('facts_sha256') == digest(payload['confirmed_facts'])
                    and staged_meta.get('citation_registry')):
                # Explicit legacy migration: verify every staged URL against
                # current accepted sources before adopting its number identity.
                registry_hint = for_article(staged,payload['confirmed_facts'])
        citation_registry = from_context(payload['confirmed_facts'],registry_hint)
        payload['citation_registry'] = citation_registry
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
変更節の冒頭は具体的な結論とし、その章に合う読者の迷いや期待を受け止めて、次の判断へつなぐ。
隣接節は文脈確認用の読取専用。必要なつながりは変更対象の節側で整える。隣接節自体に修正が必要なら、後続の全文QAの具体的な指摘に沿って局所修正する。
JSONのみを返す。''',
        messages=[{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
        output_config={'format': {'type': 'json_schema', 'schema': schema}})
    if citation_registry is not None:
        request['system'] += '\n'+CITATION_POLICY
    signature = candidate_signature(packet,outline,payload,request['system'],model)
    restored = cached_candidate(job_id,signature,outline,contract,job.get('word_count_setting'))
    if citation_registry is not None and restored is None and not original.get('meta',{}).get('citation_registry'):
        from .article_citations import integrity_issues
        if integrity_issues(source['content_text']):
            fail('旧原稿の出典番号に未対応があります。保存済み資料で対応を整えた候補が必要です。有料の全文修正は再送しません。')
    import anthropic
    client = None if is_openai_model(model) else anthropic.Anthropic(api_key=api_key, max_retries=0)
    if hybrid_writing_enabled() and restored is None:
        from .step_content_audit import repair_output_limit
        sizing = {'target_block_ids': packet['target_section_ids'], 'article_blocks': [
            {'id': sid, 'text': packet['source_sections'].get(sid, {}).get('body', '')}
            for sid in packet['target_section_ids']]}
        request['max_tokens'] = repair_output_limit(sizing, limit, lambda sample:
            client.with_options(max_retries=0).messages.count_tokens(model=model,
                messages=[{'role': 'user', 'content': sample}], timeout=30).input_tokens)
        request['output_budget_includes_thinking'] = True
    replacements = {}
    for attempt in (() if restored else range(2)):
        if packet['target_section_ids']:
            value, _ = checked_request(job_id, f'tiered_content_repair_outline_sections_{attempt}', request, client=client)
        else:
            value = {'sections': {}}  # Removing only a redundant section needs no model call.
        expected = set(request['output_config']['format']['schema']['properties']['sections']['properties'])
        if not isinstance(value.get('sections'),dict) or set(value['sections']) != expected:
            fail('修正応答の節IDが要求範囲と一致しません。原稿は変更しません。')
        replacements.update(value['sections'])
        if citation_registry is not None:
            from .article_citations import tokens
            replacements = {sid:tokens(body,citation_registry) for sid,body in replacements.items()}
        candidate, binding = assemble(packet, replacements, outline['content_text'])
        if citation_registry is not None:
            from .article_citations import render
            candidate = render(candidate,citation_registry)
        issues = []
        try:
            issues = validate_delivery(candidate, outline['content_text'], job.get('word_count_setting'), contract, binding)
            issues += readability_issues(candidate) + validate_structure(candidate, contract, outline=False)
            if cta_block and cta_block in candidate:
                issues.append({'key': 'cta_generated_in_body'})
            if issues:
                fail(json.dumps(issues, ensure_ascii=False))
            break
        except (ContentQualityError, KeyError) as exc:
            if attempt or not issues:
                raise
            request = scoped_retry(request, packet, candidate, binding, outline, issues)
    if restored:
        candidate,binding = restored
    else:
        upsert_artifact(job_id=job_id,step='outline_body_repair_candidate',content_type='text/markdown',
            content_text=candidate,meta={'input_sha256':signature,'text_sha256':digest(candidate),
                'section_map':binding,'citation_registry':citation_registry,'audited':False,
                'source_sha256':digest(original['content_text']),'outline_sha256':digest(outline['content_text']),
                'facts_sha256':digest(payload['confirmed_facts'])})
    preserved,rebound = preserved_sections(packet,candidate,binding,outline,citation_registry)
    candidate, lead_repair = finish_failed_lead(client, candidate, outline, keyword, job, report)
    meta = {**original.get('meta', {}), 'section_map': binding, 'content_audited': False,
            'content_audit_snapshot': None, 'editorial_recovery_pending': True,
            'outline_body_repair': {'version': VERSION, 'source_sha256': digest(original['content_text']),
                'lead_sha256': digest(packet['lead']), 'target_section_ids': packet['target_section_ids'],
                'preserved_section_sha256': preserved, 'removed_section_ids': packet['removed_section_ids'],
                'citation_rebound_section_ids':rebound,
                'lead_preserved': candidate.startswith(packet['lead']), 'lead_repair': lead_repair,
                'cta_reinserted_by_configured_step': bool(cta_block)}}
    if citation_registry is not None:
        from .article_citations import render
        candidate = render(candidate,citation_registry)
        meta['citation_registry'] = citation_registry
    from .article_design import correspondence, design_context
    meta['reader_correspondence'] = correspondence(candidate, outline['content_text'], design_context(outline), binding)
    # CTA positions are re-resolved against the new blueprint in the next step.
    meta.pop('cta_placement', None)
    return upsert_artifact(job_id=job_id, step='article', content_type='text/markdown',
                           content_text=candidate, meta=meta)
