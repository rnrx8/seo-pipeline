"""Chapter-sized reader checks inside coverage QA, with source-bound context."""
import copy
import hashlib
import json
import re
from types import SimpleNamespace

from . import reader_journey

VERSION = 'reader-qa-v2-context-and-descendant-scope'
BATCH_SIZE = 6
CONTEXT_POLICY = '''本文の章ごとの読者価値だけを確認する。設計は目標であり本文の実現の証拠ではない。
評価範囲は各caseのarticle_blocksにある対象章とその子孫。親章の判定では子節の説明も含め、親章の導入に独立の共感や生活上の目的を重ねて要求しない。子節が担う固有の選択は子節内で確認する。
reading_contextはリード・親章・前後で何を扱い、この章で何を新たに答えるかを判断する実本文。既に扱った実感の反復や、直接回答の節への独立した共感文を要求しない。ただし親・兄弟・後続章の文章でこの章独自の不足を相殺せず、周辺章をevidenceに引用しない。各章のarticle_blocksだけから引用する。
記事全体の背景にある不安と、この節で新たに受け止めるべき実感を区別する。元分析の大きな願望があるだけで、全節へその再掲を要求しない。
同じ記事の他の章の合否に揃えず、元分析とこの章の問い、実際の説明を読む。指定された全IDに個別のjourneyを返す。'''


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def packets(text, outline, design):
    from .article_design import correspondence
    from .content_edits import content_blocks
    mapping = correspondence(text, outline, design)
    sections = design.get('sections', [])
    rows = {r['blueprint_id']: r for r in mapping['sections']}
    designed = {s['id']: s for s in sections}
    blocks = content_blocks(text)
    first_heading = re.search(r'^#{2,4}[ \t]+', text, re.M)
    introduction = text[:first_heading.start()] if first_heading else text
    needs = {n['id']: n for n in design.get('reader_needs', [])}

    def own(sid):
        section = designed[sid]
        ids = set(rows[sid]['block_ids'])
        return {'section_id': sid, 'title': section['title'], 'question': section['question'],
                'reader_progression': copy.deepcopy(section.get('reader_progression')),
                'own_text': '\n\n'.join(b['text'] for b in blocks if b['id'] in ids)}

    cases = []
    for index, section in enumerate(sections):
        sid = section['id']; ancestors = []; parent = section.get('parent_id', '')
        while parent:
            if parent not in designed or parent in {a['section_id'] for a in ancestors}:
                from .content_quality import ContentQualityError
                raise ContentQualityError('章の読者確認に渡す親子関係が不正です。')
            ancestors.append(own(parent)); parent = designed[parent].get('parent_id', '')
        ancestor_ids = {a['section_id'] for a in ancestors}
        if needs and any(n not in needs for n in section['need_ids']):
            from .content_quality import ContentQualityError
            raise ContentQualityError('章に対応する元の読者分析が不足しています。')
        allowed = set(rows[sid]['review_block_ids'])
        descendants = [s for s in sections if s['id'] != sid and
                       set(rows[s['id']]['block_ids']) & allowed]
        case = {'id': sid, 'section': copy.deepcopy(section),
                'section_own_text': own(sid)['own_text'],
                'article_blocks': [b for b in blocks if b['id'] in allowed],
                'descendant_designs': copy.deepcopy(descendants),
                'evaluated_section_ids': [sid]+[s['id'] for s in descendants],
                'reader_needs': [copy.deepcopy(needs[n]) for n in section['need_ids'] if n in needs],
                'reading_context': {
                    'article_introduction': introduction,
                    'ancestors': list(reversed(ancestors)),
                    'previous_section': own(sections[index-1]['id']) if index and sections[index-1]['id'] not in ancestor_ids else None,
                    'next_question': {k: sections[index+1][k] for k in ('title', 'question')} if index+1 < len(sections) else None}}
        # Older validated designs may lack the new need-level projection.
        # Preserve their supplied reader material, never invent an analysis.
        if not case['reader_needs']:
            case['reader_context'] = copy.deepcopy(design.get('reader_focus', {}))
        cases.append(case)
    return [{'cases': cases[i:i+BATCH_SIZE]} for i in range(0, len(cases), BATCH_SIZE)]


def output_config(packet):
    entries = {c['id']: reader_journey.schema() for c in packet['cases']}
    return {'format': {'type': 'json_schema', 'schema': {
        'type': 'object', 'properties': {'reader_journeys': {
            'type': 'object', 'properties': entries, 'required': list(entries), 'additionalProperties': False}},
        'required': ['reader_journeys'], 'additionalProperties': False}}}


def checked(value, packet, text):
    from .content_quality import ContentQualityError
    from .content_edits import content_blocks
    if not isinstance(value, dict) or set(value) != {'reader_journeys'}:
        raise ContentQualityError('章の読者確認の応答が不整合です。')
    journeys = value['reader_journeys']
    if not isinstance(journeys, dict) or set(journeys) != {c['id'] for c in packet['cases']}:
        raise ContentQualityError('章の読者確認が不足または重複しています。')
    anchor = content_blocks(text)[:1]; result = {}
    for case in packet['cases']:
        journey = journeys[case['id']]
        missing = reader_journey.checked(journey, {b['id']: [b['text']] for b in case['article_blocks']})
        candidates = case['article_blocks'] or anchor
        if not candidates:
            raise ContentQualityError('章の読者確認対象の本文がありません。')
        locations = []
        for name in ('attunement', 'lived_value', 'guidance'):
            row = journey[name]
            if row['status'] != 'fail': continue
            ids = list(dict.fromkeys(e['id'] for e in row['evidence'])) or [candidates[0]['id']]
            for bid in ids:
                location = {'id': bid, 'reason': row['reason']}
                if location not in locations: locations.append(location)
        result[case['id']] = {'status': 'fail' if missing else 'pass',
            'reason': journey['reader_state'],
            'affected_blocks': locations, 'journey': copy.deepcopy(journey)}
    return result


def audit(client, *, text, outline, design, snapshot, model, budget):
    from .ai import tiered_review_enabled
    from .content_quality import create_with_retry, response_text
    from .tiered_research import checked_request, structured_review_output_limit
    from .quality_budget import JOB
    batches = []; reviews = {}; total_in = total_out = 0
    for index, packet in enumerate(packets(text, outline, design)):
        request = {'model': model, 'max_tokens': budget,
                   'system': CONTEXT_POLICY+'\n'+reader_journey.REVIEW_POLICY,
                   'messages': [{'role': 'user', 'content': json.dumps(packet, ensure_ascii=False)}],
                   'output_config': output_config(packet)}
        request['max_tokens'] = structured_review_output_limit(request)
        print('[quality] Checking coverage reader chapters '+str(index+1), flush=True)
        if tiered_review_enabled():
            value, usage = checked_request(JOB.get(), 'tiered_article_reader_'+str(index+1), request)
        else:
            message = create_with_retry(client, **request)
            value, usage = json.loads(response_text(message)), message.usage
        rows = checked(value, packet, text)
        reviews.update(rows); total_in += usage.input_tokens; total_out += usage.output_tokens
        batches.append({'section_ids': list(rows), 'packet_sha256': digest(packet),
                        'model': model, 'reviews': rows})
    return reviews, {'version': VERSION, 'snapshot': snapshot, 'batches': batches}, SimpleNamespace(input_tokens=total_in, output_tokens=total_out)


def require_trace(phase, *, text, outline, design, snapshot):
    from .content_quality import ContentQualityError
    from .ai import get_step_config
    trace = phase.get('reader_review_trace', {})
    expected = packets(text, outline, design)
    def fail(): raise ContentQualityError('章の読者確認の本文・文脈・個別結果が現在の原稿と一致しません。')
    if not isinstance(trace, dict) or set(trace) != {'version', 'snapshot', 'batches'} or not isinstance(trace.get('batches'), list): fail()
    if trace.get('version') != VERSION or trace.get('snapshot') != snapshot or len(trace.get('batches', [])) != len(expected): fail()
    reviews = {}
    for batch, packet in zip(trace['batches'], expected):
        ids = [c['id'] for c in packet['cases']]
        if not isinstance(batch, dict) or set(batch) != {'section_ids', 'packet_sha256', 'model', 'reviews'}: fail()
        if batch.get('section_ids') != ids or batch.get('packet_sha256') != digest(packet) or batch.get('model') != get_step_config('content_audit')[0]: fail()
        rows = batch.get('reviews', {})
        if not isinstance(rows, dict) or set(rows) != set(ids) or any(not isinstance(v, dict) for v in rows.values()): fail()
        validated = checked({'reader_journeys': {k: v.get('journey') for k, v in rows.items()}}, packet, text)
        if rows != validated: fail()
        reviews.update(rows)
    if reviews != phase.get('reader_progression_reviews'): fail()
