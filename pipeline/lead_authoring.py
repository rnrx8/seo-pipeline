"""Reader focus and a bounded lead-writing request, without body instructions.

The focus records an editorial selection, not verified audience demographics.
No accepted prose or query-specific template is included in fresh generation.
"""
import copy
import json
import re

from .authoring_policy import LEAD_POLICY
from .readability import PROSE_STYLE_POLICY

VERSION = 'focused-lead-edit-v1'


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}


STRING = {'type': 'string'}
READER_FOCUS_SCHEMA = obj({
    'need_ids': {'type': 'array', 'items': STRING},
    'motivation_ref': {'anyOf': [obj({'need_id': STRING, 'hypothesis_id': STRING,
        'link_index': {'type': 'integer', 'minimum': 0}}), {'type': 'null'}]},
    'felt_response': STRING,
    'current_understanding': STRING,
    'open_question': STRING,
    'desired_experience': STRING,
})
PLAN_SCHEMA = obj({'reader_focus': READER_FOCUS_SCHEMA})
WRITE_SCHEMA = obj({'lead': STRING})

PLANNING_POLICY = '''【記事全体の宛先を決め、リードの役割を選ぶ】
reader_focusは、ユーザーのターゲット指定と主要な検索意図から選んだ記事の主な宛先。本文へ載せる項目の一覧ではない。
need_idsには主な問いの分析ID、current_understandingには既に考えていること、open_questionには今答えを必要とする問いを記す。desired_experienceには事情の先で望む具体的な体験を記す。不要なら空文字でよい。文字列は各60字程度で、分析の短縮版ではなく今回選んだ宛先を表す。
motivation_refには、主な望みの背景を伝える中心の体験として採用するmotivation_linksの参照を一つ選ぶ。need_idとhypothesis_idを特定し、link_indexはそのmotivation_links配列の0始まり番号。選んだ原文はコードが引き渡すので、事情や他の代替候補を再要約しない。最も具体的な原因や体験へつながるリンクを選び、root_desireの抽象的分類だけで共感を作ろうとしない。心理を使わない直接回答や、構造化されたmotivation_contextのない旧分析ではnullでよい。その場合は採用したreader_situationを使う。関連する他の事情や代替手段は本文で扱え、主な宛先を無理に全員の合成像にしない。心理や場面は編集上の仮説であり、属性だけから新しい事情を作らない。
felt_responseには当人の短い気持ちだけを日常語で記す。出来事は参照元から別に渡すので再説明せず、その出来事が本人にとってどう感じられるかを考える。行動したいという意図や心理の分類だけで代替しない。気持ちを物に見立てた比喩や大げさな表現は不要。desired_experienceは、相手やサービスが心を理解してくれるという評価ではなく、本人が何をできる時間・状態を望むかを具体化する。元の事情からの心理仮説であり、読者全員の実態や出来事として断定しない。強さを勝手に増さず、不要なら空文字でよい。完成リード・例文は作らない。宛先は書き手が意識する相手であり、本文で紹介する対象ではない。
'''

WRITING_SYSTEM = '''あなたは記事の導入を担当する編集者です。完成本文へつながるリードだけをJSONで返してください。
reader_focusは語りかける相手と、その人が読み始める時点。各欄を順に文章へ変換するチェックリストではありません。その人を記事の外から紹介するのではなく、本人の考えや実感から自然に書き始めます。既に考えていることを長く解説せず、前の文を読んだその人へ次の文を届ける一つの文章として書いてください。
''' + PROSE_STYLE_POLICY + '''
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
    selected = checked_focus(value, reader_design)
    ref = selected['motivation_ref']
    needs = {n['id']: n for n in reader_design['reader_needs']}
    if ref is not None:
        branch = next(b for b in needs[ref['need_id']]['motivation_context']
                      if b['hypothesis_id'] == ref['hypothesis_id'])
        situation = branch['motivation_links'][ref['link_index']]['to']
    else:
        situation = '／'.join(needs[i].get('reader_situation', '') for i in selected['need_ids'])
    # Keep provenance in the saved design. The writer needs the actual situation,
    # not the analyst's "a reader wants ... why?" frame or internal labels.
    return {'situation': situation, 'felt_response': selected['felt_response'],
            'desired_experience': selected['desired_experience'],
            'current_understanding': selected['current_understanding'],
            'open_question': selected['open_question']}


def writing_payload(*, keyword, title, reader_focus,
                    reader_design, request, article_purpose, article_contents,
                    next_section_excerpt, facts):
    """Use an explicit focus and actual coverage, never the whole article prompt."""
    focus = focus_context(reader_focus, reader_design)
    return {'keyword': keyword, 'title': title, 'reader_focus': focus,
            'request': request,
            'article_purpose': article_purpose,
            'article_contents': copy.deepcopy(article_contents),
            'next_section_excerpt': next_section_excerpt, 'facts': facts}


def writer_request(payload, *, model, max_tokens=1200):
    return {'model': model, 'max_tokens': max_tokens, 'system': WRITING_SYSTEM,
            'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'output_config': {'format': {'type': 'json_schema', 'schema': copy.deepcopy(WRITE_SCHEMA)}}}


def lead_span(text):
    """Locate only the prose between the single H1 and the first H2."""
    from .content_quality import ContentQualityError
    h1 = list(re.finditer(r'^#\s+([^\n]+)$', text, re.M))
    h2 = re.search(r'^##\s+([^\n]+)$', text, re.M)
    if len(h1) != 1 or not h2 or h1[0].end() >= h2.start():
        raise ContentQualityError('リードだけを編集するためのタイトル・本文境界が不明です。')
    gap = text[h1[0].end():h2.start()]
    start = h1[0].end() + len(gap) - len(gap.lstrip())
    end = h2.start() - len(gap) + len(gap.rstrip())
    if start >= end:
        raise ContentQualityError('仕上げ対象のリードがありません。')
    return start, end


def replace_lead(text, lead):
    from .content_quality import ContentQualityError
    if (not isinstance(lead, str) or not lead.strip()
            or re.search(r'^\s*(?:#{1,6}\s|```|\|)', lead, re.M)):
        raise ContentQualityError('リード以外の構造を仕上げ稿へ混入できません。')
    start, end = lead_span(text)
    return text[:start] + lead.strip() + text[end:]


def editing_payload(text, value, ctx, keyword, requirements):
    start, end = lead_span(text)
    titles = re.findall(r'^##\s+([^\n]+)$', text, re.M)
    h2 = list(re.finditer(r'^##\s+', text, re.M))
    next_section = text[h2[0].start():h2[1].start() if len(h2) > 1 else len(text)]
    focus = focus_context(value['reader_focus'], ctx['reader_design'])
    return {'keyword': keyword, 'reader_focus': focus,
            'title': re.search(r'^#\s+([^\n]+)$', text, re.M).group(1),
            'article_contents': titles, 'next_section_excerpt': next_section,
            'requirements': copy.deepcopy(requirements), 'draft_lead': text[start:end],
            'editing_task': '原稿を最初から最後まで通読し、この読者へ届く一続きの導入へ仕上げる。'
                '出来事と本人の実感は残し、重なる説明や既に知っている概念の解説を整理する。'
                '主題・望み・ためらい・本文を読む理由が自然につながるよう整える。'
                '途中から読者を第三者へ解説したり、書き手の告白になったりしない。'
                '元の条件の意味を変えず、予告を本文と照合し、結果を保証しない。'
                '通常は180〜220字程度。ユーザーのリードの明示的な長さ指定を優先する。'
                '改善が不要なら原稿を保つ。'}
