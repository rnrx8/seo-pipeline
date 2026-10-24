"""Check designed chapter outcomes in the actual article, within coverage QA."""
import copy

POLICY = '''【本文の章ごとの読者への働き】
reader_progression_reviewsは、reader_progressionを持つ節ごとに返す。
構成のbefore/after/next_questionsは目標であり、実現の証明ではない。reader_correspondenceのreview_block_idsにある本文を読み、この章が受け持つ疑問へ実際に答え、指定した理解・判断・行動の助けを提供しているか確認する。reasonには実際の短い記述と、その説明で何が分かるか、何が足りないかを書く。見出し・表・機能・次章への案内があるだけでpassにしない。
親の章は子節の説明も含めて評価する。子節に独自のreader_progressionがある場合、その節の本文と差分を個別に照合し、親や兄弟節にある共感・回答で不足を相殺しない。nullの子節も、問いとanswer_elementsで扱う固有の懸念が実際の説明に反映されているかを親章の確認に含める。後続の別章の説明を、この章で答えたことにしない。各章ですべてを解決する必要はなく、後続へ割り当てた問いはこの章で答えなくてよい。その章で解く問いと次の問いの関係が自然で、案内する先に回答があるかを確かめる。章の終わりに誘導文を必ず付ける指示ではない。
感情は狙いであり実測ではない。設計した感情の語句・毎章の共感・不安の強調を要求せず、必要な説明や具体策がその節を読む時点の事情やためらいに応えているかを見る。具体策の有用性と、その人の気持ちを受け止めた表現は区別する。迷い・焦り・伝わるかという心配への受け止めが求められる節では、読者がその状況をどう受け止めて迷い・ためらっているかが本文の短い状況・条件句等から読み取れ、その気持ちと提案する回答が自然につながるかも照合する。出来事や問題が存在するという説明だけ、手順や判断材料の存在だけで、指定された気持ちの汲み取りを充足としない。感情名そのものは必須でないが、何を信じればよいか分からない等、その人の実感や心の動きが伝わる実際の語句をreasonへ示す。短い条件句で具体的な迷いを受け止めて回答へつなぐ説明は許容し、固定の共感文や語尾、感情への言及のない全節への追加を要求しない。記事全体の欲求を言い換えただけで、固有の迷いや望みへ応えたことにしない。前節で解消した疑問を未解決へ戻す反復も確認する。直接回答・手順はその回答だけで成立できる。記事目的に沿う推薦や誘導自体を欠陥にせず、選ぶ理由と必要条件が説明されるか確認する。
statusはpass/fail。failは直すべき章内の実在段落IDと修正理由をaffected_blocksへ返し、passは空配列。章が見つからない場合はfailとし、補う位置の既存見出しを指定する。記事全体のdecision_reviewsやcoverageがpassでも、この章の必要な回答不足を相殺しない。'''


def sections(design):
    return [s for s in design.get('sections', []) if s.get('reader_progression')]


def output_config(config, design):
    targets = sections(design)
    if not targets:
        return config
    result = copy.deepcopy(config)
    root = result['format']['schema']
    location = {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'reason': {'type': 'string'}},
        'required': ['id', 'reason'], 'additionalProperties': False}
    row = {'type': 'object', 'properties': {
        'status': {'type': 'string', 'enum': ['pass', 'fail']},
        'reason': {'type': 'string'},
        'affected_blocks': {'type': 'array', 'items': location}},
        'required': ['status', 'reason', 'affected_blocks'], 'additionalProperties': False}
    entries = {s['id']: copy.deepcopy(row) for s in targets}
    root['properties']['reader_progression_reviews'] = {
        'type': 'object', 'properties': entries, 'required': list(entries),
        'additionalProperties': False}
    root['required'].append('reader_progression_reviews')
    return result


def add_review_ranges(mapping, design):
    """A parent chapter can use its actual descendants, never a later sibling."""
    children = {}
    for section in design['sections']:
        children.setdefault(section.get('parent_id', ''), []).append(section['id'])
    rows = {r['blueprint_id']: r for r in mapping['sections']}

    def members(sid, seen):
        if sid in seen:
            return set()
        return {sid} | {child for direct in children.get(sid, [])
                        for child in members(direct, seen | {sid})}

    for sid, row in rows.items():
        group = members(sid, set())
        row['review_block_ids'] = list(dict.fromkeys(
            block for r in mapping['sections'] if r['blueprint_id'] in group
            for block in r['block_ids']))
    return mapping


def checked(value, design, mapping, text):
    from .content_edits import content_blocks
    from .content_quality import ContentQualityError
    targets = sections(design)
    if not targets:
        return []

    def fail():
        raise ContentQualityError('本文の章ごとの読者判断・説明範囲の確認が不足または不整合です。')

    rows = value.get('reader_progression_reviews')
    if not isinstance(rows, dict) or set(rows) != {s['id'] for s in targets}:
        fail()
    mapped = {r['blueprint_id']: r for r in mapping['sections']}
    designed = {s['id']: s for s in design['sections']}
    blocks = content_blocks(text)
    anchor = {blocks[0]['id']} if blocks else set()
    issues = []
    for section in targets:
        sid = section['id']
        row, located = rows[sid], mapped[sid]
        if (not isinstance(row, dict) or set(row) != {'status', 'reason', 'affected_blocks'}
                or row['status'] not in ('pass', 'fail')
                or not isinstance(row['reason'], str) or not row['reason'].strip()
                or not isinstance(row['affected_blocks'], list)):
            fail()
        locations = row['affected_blocks']
        if (row['status'] == 'fail') != bool(locations):
            fail()
        if not located['article_section_ids'] and row['status'] == 'pass':
            fail()
        allowed = set(located['review_block_ids']) or anchor
        if any(not isinstance(loc, dict) or loc.get('id') not in allowed
               or not isinstance(loc.get('reason'), str) or not loc['reason'].strip()
               for loc in locations):
            fail()
        if row['status'] == 'fail':
            # Keep the research behind the actual defective paragraph available
            # to scoped repair, including a child paragraph inside a parent H2.
            ids = set(section.get('research_ids', []))
            for loc in locations:
                for other_sid, other in mapped.items():
                    if loc['id'] in other['block_ids']:
                        ids.update(designed[other_sid].get('research_ids', []))
            issues.append({'section_id': sid, 'title': section['title'],
                           'reason': row['reason'], 'affected_blocks': copy.deepcopy(locations),
                           'research_ids': sorted(ids)})
    return issues


def findings(issues):
    return [{'check_key': 'coverage', 'severity': 'blocking',
             'block_ids': [loc['id'] for loc in issue['affected_blocks']],
             'problem': issue['reason'],
             'reader_impact': '「' + issue['title'] + '」で受け持つ疑問・判断への説明が不足している。',
             'required_change': 'この章で必要な回答を根拠と条件に沿って補う。後続の別の問いや定型の共感は足さない。',
             'stage_to_fix': 'investigate', 'section_id': issue['section_id'],
             'origin': 'individual_article_progression_review'} for issue in issues]
