"""Reader focus and a bounded lead-writing request, without body instructions.

The focus records an editorial selection, not verified audience demographics.
No accepted prose or query-specific template is included in fresh generation.
"""
import copy
import json
import re

from .readability import PROSE_STYLE_POLICY
from .authoring_policy import LEAD_POLICY

VERSION = 'focused-lead-v3-shared-authoring-policy'


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}


STRING = {'type': 'string'}
LEAD_DIRECTIONS = {
    'reader_experience': '中心に選んだ出来事と本人の実感から、望みと今の問い、本文で得る判断材料へ進む。',
    'desired_change': '望む具体的な体験から、それを得るために今知りたい問いと、本文で得る判断材料へ進む。',
    'decision_difficulty': '今決められない問いと迷う理由から、本文で何を確かめれば判断できるかへ進む。',
    'direct_answer': '今知りたい問いへ短く答え、必要な理由・条件と本文で確認する詳細へ進む。',
}
DIRECTION_SCHEMA = {'type': 'string', 'enum': list(LEAD_DIRECTIONS.values())}
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
motivation_refには、主な望みの背景を伝える中心の体験として採用するmotivation_linksの参照を一つ選ぶ。need_idとhypothesis_idを特定し、link_indexはそのmotivation_links配列の0始まり番号。選んだ原文はコードが引き渡すので、事情や他の代替候補を再要約しない。主題の対象や体験を望む理由と、その手段を選ぶ際の迷い・制約を分ける。読者理解を使う入口では、その対象や体験を求める背景の、具体的な原因や本人の体験へつながるリンクを選ぶ。探しにくさや選ぶ際の不安だけで、その対象を求める理由を代替しない。root_desireの抽象的分類だけで共感を作ろうとしない。心理を使わない直接回答や、構造化されたmotivation_contextのない旧分析ではnullでよい。その場合は採用したreader_situationを使う。関連する他の事情や代替手段は本文で扱え、主な宛先を無理に全員の合成像にしない。心理や場面は編集上の仮説であり、属性だけから新しい事情を作らない。
felt_responseには当人の短い気持ちだけを日常語で記す。出来事は参照元から別に渡すので再説明せず、その出来事が本人にとってどう感じられるかを考える。行動したいという意図や心理の分類だけで代替しない。気持ちを物に見立てた比喩や大げさな表現は不要。desired_experienceは、相手やサービスが心を理解してくれるという評価ではなく、本人が何をできる時間・状態を望むかを具体化する。元の事情からの心理仮説であり、読者全員の実態や出来事として断定しない。強さを勝手に増さず、不要なら空文字でよい。完成リード・例文は作らない。宛先は書き手が意識する相手であり、本文で紹介する対象ではない。
'''

WRITING_SYSTEM = '''あなたは記事の導入を担当する編集者です。完成本文へつながるリードだけをJSONで返してください。
書き手は、読者を案内する編集者です。reader_focusは語りかける相手と読み始める時点です。感情描写はその事情のある読者のものとして語り、説明と予告は編集者から読者へ届けます。短い実感の代弁は使えますが、書き手自身の出来事・願望を連ねる独白にしません。
''' + PROSE_STYLE_POLICY + LEAD_POLICY + '''
語り方の例（架空の別題材。今回の事情・語句へ転用する素材ではありません）：「弾いてみたい曲はあるけれど、楽譜を開くと難しそうで手が止まる。一人で練習を続けられるかも少し心配。そんな気持ちで大人向けのピアノ教室を探す人もいるでしょう。」読者の実感を短く代弁しながら、誰の思いなのかと主題の関係が分かる距離で語れます。固定の書き出し・文数・接続詞・人称を真似る指示ではありません。
数値・固有の事実・頻度・結果の保証を創作せず、本文にない回答を予告しないでください。記事目的とユーザーが指定した推薦の位置を守り、本文で何を確認・比較・判断できるかへつなぎます。見出し・表・CTA・編集メモは出力しません。
'''


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


def writer_request(payload, *, model, max_tokens=1200):
    return {'model': model, 'max_tokens': max_tokens, 'system': WRITING_SYSTEM,
            'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'output_config': {'format': {'type': 'json_schema', 'schema': copy.deepcopy(WRITE_SCHEMA)}}}


def lead_char_limit(requirements):
    """Bound the default brief; honor explicit lead lengths, not body lengths."""
    text = requirements.get('custom_prompt') or ''
    for pattern in (r'リード(?:文)?[^。\n\d]{0,12}(\d{2,4})[〜～~\-](\d{2,4})(?:字|文字)',
                    r'(\d{2,4})[〜～~\-](\d{2,4})(?:字|文字)(?:の)?リード'):
        match = re.search(pattern, text)
        if match and not re.search('本文|記事|全体',match.group(0)):
            return max(int(n) for n in match.groups())
    for pattern in (r'リード(?:文)?[^。\n\d]{0,12}(\d{2,4})(?:字|文字)(前後|程度|以内)?',
                    r'(\d{2,4})(?:字|文字)(前後|程度|以内)?(?:の)?リード'):
        match = re.search(pattern, text)
        if match and not re.search('本文|記事|全体',match.group(0)):
            target = int(match.group(1))
            return target if match.group(2) == '以内' else (target * 11 + 9) // 10
    return 220


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
    lead_span(text)
    titles = re.findall(r'^##\s+([^\n]+)$', text, re.M)
    h2 = list(re.finditer(r'^##\s+', text, re.M))
    next_section = text[h2[0].start():h2[1].start() if len(h2) > 1 else len(text)]
    focus = focus_context(value['reader_focus'], ctx['reader_design'])
    return {'keyword': keyword, 'reader_focus': focus,
            'title': re.search(r'^#\s+([^\n]+)$', text, re.M).group(1),
            'article_contents': titles, 'next_section_excerpt': next_section,
            'lead_direction': value['lead_direction'],
            'requirements': copy.deepcopy(requirements),
            'editing_task': '読者設計と実際の本文に合わせ、リードを専用に執筆する。'
                '出来事と本人の実感を結び、重なる説明や既に知っている概念の解説を整理する。'
                '主題・望み・ためらい・本文を読む理由が自然につながるよう整える。'
                '読者の事情と書き手の立場を区別し、読み手を案内する文章にする。'
                '元の条件の意味を変えず、予告は本文で比較・確認・判断できることを約束する。'
                '本文を読んだ後の成果や状況の実現を保証する予告にしない。'
                '通常は180〜220字程度。ユーザーのリードの明示的な長さ指定を優先する。'
                '出力前に一段落として通読してから完成稿を返す。'}
