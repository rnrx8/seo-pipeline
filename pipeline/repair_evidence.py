"""Exact source excerpts carried from factual review to local repair."""
import copy

VERSION = 'repair-evidence-v1'
POLICY = '''
各checkにcorrection_evidenceを返す。修正内容が原文から確定したfailでは、訂正に使うURLと連続した原文quoteを残す。
quoteには対象・プラン・期間・金額・例外等、訂正に必要な条件を含め、別の箇所なら複数引用する。要約や改変した文を引用にしない。
訂正する値が必要なら値そのものの根拠を残す。「料金を追加する」とだけ指示して金額の根拠を省かない。
pass、または資料確認が未完了の場合は空配列でよい。修正担当はこの引用と採用済み回答で修正し、資料全ページの再探索はしない。
'''


def add_schema(schema, urls):
    row = schema['format']['schema']['properties']['checks']['items']
    row['properties']['correction_evidence'] = {'type':'array','maxItems':8,'items':{
        'type':'object','properties':{'url':{'type':'string','enum':sorted(urls)},
        'quote':{'type':'string','minLength':1,'maxLength':1800}},
        'required':['url','quote'],'additionalProperties':False}}
    row['required'].append('correction_evidence')


def validate(checks, pages, *, unresolved):
    from .review_scope import canonical, fail
    bodies = {canonical(p['url']):p.get('text','') for p in pages}
    for check in checks:
        refs = check.get('correction_evidence', [])
        if not isinstance(refs,list) or (check['status']=='fail' and not unresolved and not refs):
            fail('訂正に必要な原文引用がありません。資料全文を修正担当へ転送しません。')
        for ref in refs:
            if (not isinstance(ref,dict) or not isinstance(ref.get('url'),str)
                or not isinstance(ref.get('quote'),str) or not ref['quote'].strip()
                or ref['quote'] not in bodies.get(canonical(ref['url']),'')):
                fail('訂正用引用が提示原文と一致しません。未確認の訂正を渡しません。')
        check['correction_evidence'] = copy.deepcopy(refs)


def compact_repair_sources(packet, report):
    from .review_scope import fail
    trace = list(report.get('evidence_routing', []))
    trace.extend(t for phase in report.get('phases', {}).values() for t in phase.get('evidence_routing', []))
    if trace and any(t.get('repair_evidence_version') != VERSION for t in trace):
        fail('保存済み照合結果に訂正用引用がありません。該当範囲の照合更新が必要です。全資料を再送しません。')
    if report.get('evidence_pending') or any(c.get('requires_source_check') for c in report['checks'] if c['status']=='fail'):
        fail('原文照合が未完了のため局所修正へ進めません。')
    checks = [c for c in report['checks'] if c['status']=='fail']
    # Check sources again against this exact repair snapshot. Model text cannot
    # become a quotation merely because a receipt or prior report contains it.
    validate([c for c in checks if 'correction_evidence' in c], packet['source_documents'], unresolved=False)
    # Accepted answers and their conditions are already in requirements. New
    # factual corrections refer to a deduplicated exact-quote catalog.
    refs = {}; index = {}
    for check in packet['failed_checks']:
        ids = []
        for ref in check.pop('correction_evidence', []):
            key = (ref['url'], ref['quote'])
            if key not in index:
                sid = 'correction-' + str(len(index) + 1)
                index[key] = sid; refs[sid] = copy.deepcopy(ref)
            if index[key] not in ids: ids.append(index[key])
        if ids: check['correction_source_ids'] = ids
    packet['correction_sources'] = refs
    packet['source_documents'] = []
    packet.pop('candidate_checks', None)
    packet['repair_evidence_version'] = VERSION
