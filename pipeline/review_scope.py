"""Explicit source-review scope shared by outline, article, and repairs.

No semantic matching by keywords: the whole-document reviewer supplies addresses
and dependencies. Missing addresses stop expansion, rather than selecting all data.
"""
import copy
import json
from datetime import datetime, timezone

POLICY_VERSION = 'scoped-review-v2-preserve-source-set'
ROUTING_POLICY = '''
【今回の判定段階：上記の原文照合指定より優先】
採用済み回答と条件、source_resolutions、source_excerptsを用いて現在の文書を判定する。
source_excerptsは取得資料と一致を確認した原文抜粋。修正前の合否ではなく、現在の主張の対象・プラン・期間・金額を照合する資料として使う。抜粋で確認できない条件だけ追加の原文照合へ回す。
採用済み回答と条件が一致する主張は、原文がここに無いだけで不合格にしない。
各checkにaffected_blocks、research_ids、source_urls、requires_source_checkを返す。
failは該当する全段落（同じ主張の表・冒頭・結論を含む）をaffected_blocksに列挙する。
research_idsはその判断・修正に関係するresearch_decisions.itemsのid、source_urlsは必要な出典URL。
原文なしでも誤りと訂正が確定する場合requires_source_check=false。正誤が不明な事実項目だけtrue。
trueなら必要なsource_urlsまたはresearch_idsと、具体的な未解決条件を必ず指定する。
pass/not_applicableのaffected_blocksは空、requires_source_checkはfalse。
引用禁止・使用しない情報の指示を、本文で断定する主張と取り違えない。
source_resolutionsは指定段落・条件・資料での以前の判定。failは訂正根拠であって合格ではない。
別の対象・プラン・期間の主張へ判定を転用しない。未知の資料は推測して指定しない。
'''


def fail(message):
    from .content_quality import ContentQualityError
    raise ContentQualityError(message)


def canonical(url):
    from .fresh_sources import normalize_url
    return normalize_url(url)


def pages_from(sources):
    try:
        pages = json.loads(sources) if isinstance(sources, str) else sources
        if not isinstance(pages, list) or any(not isinstance(p, dict) or not p.get('url')
                or not isinstance(p.get('text'), str) for p in pages):
            raise ValueError()
        return pages
    except (ValueError, TypeError):
        fail('照合用の資料形式が不正です。')


def validate_routes(checks, blocks, requirements, pages, evidence_keys):
    ids = {b['id'] for b in blocks}
    qids = {q['id'] for q in requirements.get('research_decisions', {}).get('items', [])}
    urls = {canonical(p['url']) for p in pages}
    for c in checks:
        locs = c.get('affected_blocks')
        refs, questions = c.get('source_urls'), c.get('research_ids')
        needed = c.get('requires_source_check')
        if (type(needed) is not bool or not isinstance(locs, list)
                or not isinstance(refs, list) or not isinstance(questions, list)
                or any(not isinstance(l, dict) or l.get('id') not in ids
                       or not isinstance(l.get('reason'), str) or not l['reason'].strip() for l in locs)
                or any(not isinstance(u, str) or canonical(u) not in urls for u in refs)
                or any(not isinstance(q, str) or q not in qids for q in questions)
                or (c['status'] == 'fail' and not locs)
                or (c['status'] != 'fail' and (locs or needed))
                or (needed and (c['key'] not in evidence_keys or not (refs or questions)))):
            fail('確認・修正の対象段落と根拠の指定が不足または不正です。全資料へは拡大しません。')


def scoped_packet(payload, checks):
    """Include named dependencies and neighboring context, never unrelated bodies."""
    blocks = payload['article_blocks']
    targets = {l['id'] for c in checks for l in c.get('affected_blocks', [])}
    if not targets or not targets <= {b['id'] for b in blocks}:
        fail('対象段落が特定されていません。全文修正・全資料照合には戻しません。')
    positions = {i for i, b in enumerate(blocks) if b['id'] in targets}
    nearby = {i+d for i in positions for d in (-1, 0, 1) if 0 <= i+d < len(blocks)}
    req = copy.deepcopy(payload.get('requirements', {}))
    req.pop('source_resolutions',None)
    qids = {q for c in checks for q in c.get('research_ids', [])}
    urls = {canonical(u) for c in checks for u in c.get('source_urls', [])}
    # A comparison paragraph may cite every service. Its URLs are not evidence
    # dependencies for a question about one service. Only reviewer-named facts
    # and sources select bodies; paragraph links remain visible as context.
    rows = req.get('research_decisions', {}).get('items', [])
    selected_rows = [q for q in rows if q['id'] in qids] if qids else [q for q in rows
        if any(canonical(r['url']) in urls for r in q.get('evidence', q.get('evidence_sources', [])) if r.get('url'))]
    for q in selected_rows:
        urls.update(canonical(r['url']) for r in q.get('evidence', q.get('evidence_sources', [])) if r.get('url'))
    if 'research_decisions' in req:
        req['research_decisions'] = {'items': selected_rows}
    if 'research_plan' in req:
        req['research_plan'] = {k:v for k,v in req['research_plan'].items() if k != 'items'} | {
            'items':[q for q in payload['requirements']['research_plan'].get('items', [])
                     if q['id'] in {r['id'] for r in selected_rows}]}
    pages = [p for p in pages_from(payload.get('source_documents', [])) if canonical(p['url']) in urls]
    result = {k:copy.deepcopy(v) for k,v in payload.items()
              if k not in ('article_blocks','source_documents','requirements','confirmed_facts','outline','candidate_checks')}
    heading_ids=set()
    stack={}
    import re
    fenced=False
    for i,b in enumerate(blocks):
        # Markdown headings need not have an empty line before them. Keep
        # paragraph addresses unchanged while reading every heading line.
        for line in b['text'].splitlines():
            if line.lstrip().startswith(('```','~~~')):
                fenced=not fenced
                continue
            match=re.match(r'^[ \t]{0,3}(#{1,6})[ \t]+',line) if not fenced else None
            if match:
                depth=len(match[1]);stack={k:v for k,v in stack.items() if k<depth};stack[depth]=b['id']
            if i in nearby:heading_ids.update(stack.values())
        if i in nearby:heading_ids.update(stack.values())
    result.update(article_blocks=[b for i,b in enumerate(blocks) if i in nearby],
                  target_block_ids=sorted(targets), requirements=req, source_documents=pages,
                  candidate_checks=checks,
                  heading_context=[b for b in blocks if b['id'] in heading_ids])
    return result


def receipt_binding(blocks, pages, requirements, checks, stage="research"):
    from .content_quality import digest, AUDIT_SYSTEM
    from .tiered_evidence import POLICY as SOURCE_POLICY
    from .quality_context import review_reference_date
    # Include subject conditions, settings, reference date, and neighboring context.
    req = {k:v for k,v in requirements.items() if k != 'source_resolutions'}
    return digest(json.dumps([POLICY_VERSION,ROUTING_POLICY,AUDIT_SYSTEM,SOURCE_POLICY, review_reference_date(stage),
        blocks, pages, req, checks], ensure_ascii=False, sort_keys=True))


def source_resolutions(stage, blocks, sources, requirements):
    """Read prior source decisions from the existing evidence response artifact."""
    from .quality_budget import JOB
    from .tiered_research import get_optional_artifact
    if not JOB.get():
        return []
    artifact = get_optional_artifact(JOB.get(), f'tiered_{stage}_evidence_screen')
    receipts = (artifact or {}).get('meta', {}).get('source_resolutions', [])
    valid = []
    for r in receipts:
        # Legacy failed receipts describe a defect but omit the exact correction
        # evidence. Reuse passing receipts; refresh only incomplete failed ones.
        if any(c['status']=='fail' and not c.get('correction_evidence') for c in r['checks']):
            continue
        if not {l['id'] for c in r['scope'] for l in c.get('affected_blocks',[])} <= {b['id'] for b in blocks}:
            continue
        packet = scoped_packet({'article_blocks':blocks,'source_documents':sources,'requirements':requirements}, r['scope'])
        binding = receipt_binding(packet['article_blocks'] + packet['heading_context'], packet['source_documents'], packet['requirements'],r['scope'],stage)
        if binding == r.get('binding'):
            from .tiered_evidence import validate_claim_verdicts
            from .content_quality import ContentQualityError
            try:
                validate_claim_verdicts(r['checks'], r['scope'])
            except ContentQualityError:
                continue
            valid.append(r)
    return valid


def source_excerpts(stage, sources):
    """Carry source text across document edits, never carry the old verdict.

    A review decision is bound to article blocks; a source excerpt is bound
    to the fetched page. Keep these identities separate so a repaired claim
    can be checked without requesting the same page again.
    """
    from .quality_budget import JOB
    from .tiered_research import get_optional_artifact
    from .content_quality import digest
    if not JOB.get():return []
    pages={canonical(p['url']):p for p in pages_from(sources)}
    stages=('research','article') if stage=='article' else (stage,)
    receipts=[]
    for source_stage in stages:
        artifact=get_optional_artifact(JOB.get(),f'tiered_{source_stage}_evidence_screen')
        receipts.extend((artifact or {}).get('meta',{}).get('source_resolutions',[]))
    excerpts={}
    for receipt in receipts:
        snapshots=receipt.get('source_snapshots',{})
        for check in receipt['checks']:
            for ref in check.get('correction_evidence',[]):
                url=canonical(ref['url']);page=pages.get(url)
                if (not page or snapshots.get(url)!=digest(json.dumps(page,ensure_ascii=False,sort_keys=True))
                        or ref['quote'] not in page['text']):continue
                excerpts[(url,ref['quote'])]={'url':page['url'],'quote':ref['quote']}
    return list(excerpts.values())


def save_resolution(stage, packet, checks, original, trace):
    from .quality_budget import JOB
    from .content_quality import digest
    from .tiered_research import get_optional_artifact, upsert_artifact
    if not JOB.get() or trace[-1]['review']['needed']:
        return
    step = f'tiered_{stage}_evidence_screen'
    old = get_optional_artifact(JOB.get(), step)
    if not old:
        return
    meta = copy.deepcopy(old.get('meta', {}))
    additions=[]
    # Store independently addressable resolved groups as well as the complete
    # scope. A correction in another group must not discard this proof.
    scopes=[packet['candidate_checks']]
    for target in packet['target_block_ids']:
        scopes.append([{**c,'affected_blocks':[l for l in c['affected_blocks'] if l['id']==target]}
                       for c in packet['candidate_checks'] if any(l['id']==target for l in c['affected_blocks'])])
    for scope in scopes:
        view=scoped_packet(original,scope)
        binding=receipt_binding(view['article_blocks']+view['heading_context'],view['source_documents'],view['requirements'],scope,stage)
        relevant=set(view['target_block_ids'])
        verdicts=[]
        for c in checks:
            locations=[l for l in c['affected_blocks'] if l['id'] in relevant]
            decisions=[r for r in c.get('claim_verdicts',[]) if r['id'] in relevant]
            verdicts.append(c if scope==packet['candidate_checks'] else
                {**c,'affected_blocks':locations,'status':'fail' if locations else 'pass',
                 'reason':' / '.join(r['reason'] for r in decisions) or '今回の対象に指摘なし',
                 'claim_verdicts':decisions,
                 'correction_evidence':c.get('correction_evidence',[]) if locations else []})
        additions.append({'binding':binding,'scope':scope,
            'reviewed_blocks':[b for b in view['article_blocks'] if b['id'] in relevant],
            'checks':verdicts,'source_urls':[p['url'] for p in view['source_documents']],
            'source_snapshots':{canonical(p['url']):digest(
                json.dumps(p,ensure_ascii=False,sort_keys=True)) for p in view['source_documents']}})
    indexed={r['binding']:r for r in meta.get('source_resolutions',[])}
    indexed.update({r['binding']:r for r in additions})
    meta['source_resolutions']=list(indexed.values())
    upsert_artifact(job_id=JOB.get(), step=step, content_type='application/json',
                    content_text=old['content_text'], meta=meta)


def repair_packet(text, requirements, sources, report, *, planning=False):
    from .content_edits import content_blocks
    failed = [copy.deepcopy(c) for c in report['checks'] if c['status']=='fail']
    # The independent source reviewer already identified exact correction
    # quotes. Its earlier screening scope is not the local edit's fact scope.
    # Keep adopted answers attached to those quotes, not every screened fact.
    if not planning:
        rows=requirements.get('research_decisions',{}).get('items',[])
        for check in failed:
            refs=check.get('correction_evidence',[])
            if refs:
                urls={canonical(ref['url']) for ref in refs}
                related={row['id'] for row in rows
                         if any(canonical(ref['url']) in urls
                                for ref in row.get('evidence',row.get('evidence_sources',[])) if ref.get('url'))}
                if related:
                    check['research_ids']=sorted(related)
                    check['source_urls']=sorted(urls)
    from .readability import readability_issues
    # Derive whole prose runs from the exact edit input, including standalone
    # focused-review reports. Do not trust cached paragraph addresses.
    issues = [i for i in report.get('structural_issues', []) if i.get('key') != 'continuous_prose']
    issues += readability_issues(text)
    if any(not c.get('affected_blocks') for c in failed+issues):
        fail('修正箇所を特定できない指摘があります。全文修正を自動実行せず対象を確認してください。')
    blocks = content_blocks(text)
    by_id = {b['id']: b for b in blocks}
    primary = {l['id'] for c in failed+issues for l in c.get('affected_blocks', [])}
    # Reviewers may name a condition's destination in a finding without marking
    # that destination as defective. Keep those explicit dependencies editable.
    import re
    related = {ref for c in failed+issues for loc in c.get('affected_blocks', [])
               for ref in re.findall(r'(?<![A-Za-z0-9_-])block-\d+(?!\d)', loc['reason'])}
    if not related <= by_id.keys():
        fail('修正指示が参照する段落IDが現在の本文にありません。')
    # Updating a table can require its immediately preceding prose introduction
    # to change too. Neighboring context elsewhere remains read-only.
    for i, block in enumerate(blocks):
        if block['id'] not in primary|related or not i:
            continue
        if re.search(r'(?m)^\s*\|?\s*:?-{3,}:?\s*\|', block['text']):
            previous = blocks[i-1]
            if not re.match(r'^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\|)', previous['text'].lstrip()):
                related.add(previous['id'])
    dependencies = related-primary
    scope_checks = failed+issues
    if dependencies:
        scope_checks = scope_checks + [{'affected_blocks':[
            {'id':ref, 'reason':'既存指摘の参照先または表の導入を整合させるための関連段落'}
            for ref in sorted(dependencies)]}]
    packet = scoped_packet({'article_blocks':blocks,'requirements':requirements,
                            'source_documents':sources},scope_checks)
    packet['related_block_ids'] = sorted(dependencies)
    from .generation_context import repair_requirements
    packet['requirements']=repair_requirements(packet.get('requirements',{}),planning=planning)
    packet['structural_issues']=issues
    packet['failed_checks']=failed
    from .repair_evidence import compact_repair_sources
    compact_repair_sources(packet, report)
    return packet


def validate_edits(raw, packet):
    try:
        edits=json.loads(raw)['edits']
        outside = any(e['id'] not in packet['target_block_ids'] for e in edits)
    except (ValueError,KeyError,TypeError):
        fail('局所修正の形式が不正です。')
    if outside:
        fail('指摘対象外の段落を変更する修正は適用しません。')
