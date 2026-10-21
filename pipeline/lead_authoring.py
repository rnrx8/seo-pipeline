"""Reader focus and a bounded lead-writing request, without body instructions.

The focus records an editorial selection, not verified audience demographics.
No accepted prose or query-specific template is included in fresh generation.
"""
import copy
import json

from .authoring_policy import LEAD_POLICY


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}


STRING = {'type': 'string'}
READER_FOCUS_SCHEMA = obj({
    'need_ids': {'type': 'array', 'items': STRING},
    'motivation_ref': {'anyOf': [obj({'need_id': STRING, 'hypothesis_id': STRING,
        'link_index': {'type': 'integer', 'minimum': 0}}), {'type': 'null'}]},
    'current_understanding': STRING,
    'open_question': STRING,
    'desired_experience': STRING,
})
PLAN_SCHEMA = obj({'reader_focus': READER_FOCUS_SCHEMA, 'lead_direction': STRING})
WRITE_SCHEMA = obj({'lead': STRING})

PLANNING_POLICY = '''【記事全体の宛先を決め、リードの役割を選ぶ】
reader_focusは、ユーザーのターゲット指定と主要な検索意図から選んだ記事の主な宛先。本文へ載せる項目の一覧ではない。
need_idsには主な問いの分析ID、current_understandingには既に考えていること、open_questionには今答えを必要とする問いを記す。desired_experienceには事情の先で望む具体的な体験を記す。不要なら空文字でよい。文字列は各60字程度で、分析の短縮版ではなく今回選んだ宛先を表す。
motivation_refには、主な望みの背景を伝える中心の体験として採用するmotivation_linksの参照を一つ選ぶ。need_idとhypothesis_idを特定し、link_indexはそのmotivation_links配列の0始まり番号。選んだ原文はコードが引き渡すので、事情や他の代替候補を再要約しない。最も具体的な原因や体験へつながるリンクを選び、root_desireの抽象的分類だけで共感を作ろうとしない。心理を使わない直接回答や、構造化されたmotivation_contextのない旧分析ではnullでよい。その場合は採用したreader_situationを使う。関連する他の事情や代替手段は本文で扱え、主な宛先を無理に全員の合成像にしない。心理や場面は編集上の仮説であり、属性だけから新しい事情を作らない。
lead_directionは100字程度で、その人が最初に受け取りたいことから、本文へどう進めるかという編集判断だけを記す。reader_focusや分析を再度要約しない。宛先は書き手が意識する相手であり、「この人向けの記事」と本文で紹介する対象ではない。既にその望みを持つ本人へ書くので、本人の存在や動機を第三者へ証明する説明は不要。リードに必要な原因は残すが、全ての代替手段を先回りして退けない。完成リード・例文・語句指定は作らない。記事目的と実際の本文の回答を保ち、感情導入・直接回答・前向きな期待などは意図に合わせて選ぶ。
'''

WRITING_SYSTEM = '''あなたは記事の導入を担当する編集者です。完成本文へつながるリードだけをJSONで返してください。
reader_focusは語りかける相手と、その人が読み始める時点。各欄を順に文章へ変換するチェックリストではありません。その人を記事の外から紹介するのではなく、本人の考えや実感から自然に書き始めます。lead_directionに沿い、前の文を読んだその人へ次の文を届ける一つの文章として書いてください。
説明文はです・ます調を基本にします。読者自身の短い実感や望みを表す部分には、文脈が自然なら常体・体言止めを使えます。短い実感を「〜ことがあります」「〜と感じる人がいます」の説明へ一律に変換する必要はありません。視点を保ち、書き手自身の体験談にはしません。
数値・固有の事実・結果の保証を創作せず、本文にない回答を予告しないでください。見出し・表・CTA・編集メモは出力しません。
''' + LEAD_POLICY


def checked_focus(value, reader_design):
    """Validate selection provenance; this does not judge the prose's quality."""
    from .editorial_blueprint import validate_shape
    from .content_quality import ContentQualityError
    validate_shape(value, READER_FOCUS_SCHEMA)
    ids = value['need_ids']
    allowed = {n['id'] for n in reader_design.get('reader_needs', [])}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= allowed:
        raise ContentQualityError('記事の宛先が採用した読者の問いと対応していません。')
    for key in ('current_understanding', 'open_question'):
        if not value[key].strip():
            raise ContentQualityError('記事の宛先と読み始める時点が不足しています。')
    ref = value['motivation_ref']
    if ref is not None:
        need = next((n for n in reader_design['reader_needs'] if n['id'] == ref['need_id']), None)
        contexts = (need or {}).get('motivation_context', [])
        branch = next((b for b in contexts if b['hypothesis_id'] == ref['hypothesis_id']), None)
        if (ref['need_id'] not in ids or branch is None
                or ref['link_index'] >= len(branch.get('motivation_links', []))):
            raise ContentQualityError('記事の宛先が参照する背景が元の分析にありません。')
    return copy.deepcopy(value)


def focus_context(value, reader_design):
    focus = checked_focus(value, reader_design)
    ref = focus.pop('motivation_ref')
    needs = {n['id']: n for n in reader_design['reader_needs']}
    if ref is not None:
        branch = next(b for b in needs[ref['need_id']]['motivation_context']
                      if b['hypothesis_id'] == ref['hypothesis_id'])
        focus['motivating_experience'] = copy.deepcopy(branch['motivation_links'][ref['link_index']])
        focus['motivation_ref'] = ref
    else:
        focus['reader_situations'] = [needs[i].get('reader_situation', '') for i in focus['need_ids']]
    return focus


def writing_payload(*, keyword, title, reader_focus, lead_direction,
                    reader_design, request, article_purpose, article_contents,
                    next_section_excerpt, facts):
    """Use an explicit focus and actual coverage, never the whole article prompt."""
    focus = focus_context(reader_focus, reader_design)
    if not isinstance(lead_direction, str) or not lead_direction.strip():
        raise ValueError('The lead needs its editorial direction')
    return {'keyword': keyword, 'title': title, 'reader_focus': focus,
            'lead_direction': lead_direction, 'request': request,
            'article_purpose': article_purpose,
            'article_contents': copy.deepcopy(article_contents),
            'next_section_excerpt': next_section_excerpt, 'facts': facts}


def writer_request(payload, *, model, max_tokens=1200):
    return {'model': model, 'max_tokens': max_tokens, 'system': WRITING_SYSTEM,
            'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'output_config': {'format': {'type': 'json_schema', 'schema': copy.deepcopy(WRITE_SCHEMA)}}}
