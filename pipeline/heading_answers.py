"""Pin each actual heading and its own first answer to the organization review."""
import copy
import re

# Shared by authoring and QA. This is a semantic rule, not a phrase blacklist.
WRITING_POLICY = '''【見出しと直下の答え】
H2/H3/H4の第一文で、その節の問いへの具体的な答えを伝える。「方法によって異なる」「以下で紹介する」だけでは、後ろの表・一覧に答えがあっても結論先行を満たさない。条件別なら第一文で主要な分かれ方や選ぶ基準を簡潔に示し、細かな例外・全項目は後続で説明する。一文に全情報を詰め込む必要はない。短い条件句と同じ文で答える形は使える。推薦を担う節は、共通の提示基準どおり冒頭1〜2文で推薦を明示する。第一文が問いへの具体的な回答（探す方法の実名や仕組みなど）、第二文が推薦対象と結論なら合格であり、見出しに「おすすめ」とあることだけで推薦も第一文へ詰め込ませない。第一文が中身のない予告・共感だけの場合や、候補を挙げるだけで冒頭1〜2文に推薦がない場合とは区別する。見出しの言葉も、何を指し、何を勧めるかが文脈上明確であること。後続本文で説明できることを、意味の曖昧な見出しを残す理由にしない。問いの対象を明確にすることと、見出しに長い説明を詰め込むことは別。リードはこの規則の対象外。'''

POLICY = WRITING_POLICY + '''
heading_answer_entriesは実本文から切り出した見出しと、その節自身のopening_quote（第一文、文がない場合は最初の行）、opening_context（直下の段落）です。全件についてheading_answer_reviewsへ、headingとopeningを独立にpass/fail判定し、具体的な理由を記す。opening_quoteは指定値をそのまま返し、後の文・一覧・子節に差し替えない。第一文の具体的な答えを確認し、推薦節ではopening_contextも読み、第一文の具体的な回答と第二文の推薦が冒頭1〜2文の基準を満たすか見る。「推薦が第二文だから」という理由だけでfailにしない。第一文に具体的な回答がない場合を、後続の回答で相殺しない。冒頭が表やリストのみ、本文がない場合も第一文の要件を満たしたことにしない。条件依存の問いでも、第一文に具体的な分岐・判断基準があればpassにできる。感情語・推薦を全節へ追加せず、その節の回答を明確にする。headingは対象・行為が別の意味にも読めて誤解させないかを見る。本文から書き手の意図が分かることだけで曖昧な見出しをpassにしない。意味が自然に定まる省略や通常の専門語は許容する。この個別確認は文言と答えの置き方を扱い、failは既存のarticle修正へ渡す。問いの変更・回答範囲・階層そのものの問題は既存のheading_scope_reviews/editorial_findingsでoutlineへ指摘する。'''


def first_sentence(paragraph):
    """Keep punctuation inside quotations, brackets and inline code in one sentence."""
    stack = []
    pairs = {'「': '」', '『': '』', '（': '）', '(': ')', '【': '】', '[': ']', '“': '”'}
    code = False
    for i, char in enumerate(paragraph):
        if i and paragraph[i - 1] == '\\':
            continue
        if char == '`':
            code = not code
            continue
        if code:
            continue
        if stack and char == stack[-1]:
            stack.pop()
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if stack:
            continue
        if char in '。！？!?' or (char == '.' and (i + 1 == len(paragraph) or paragraph[i + 1].isspace())):
            end = i + 1
            if paragraph[end:end + 2] == '**':
                end += 2
            return paragraph[:end]
    return paragraph


def entries(text):
    """Extract own openings by offsets, even when a block contains two sections."""
    from .content_edits import content_blocks
    visible = []
    fenced = False
    for line in text.splitlines(keepends=True):
        fence = line.lstrip().startswith(('```', '~~~'))
        visible.append(re.sub(r'[^\r\n]', ' ', line) if fenced or fence else line)
        if fence:
            fenced = not fenced
    matches = list(re.finditer(r'^(#{2,4})[ \t]+(.+)$', ''.join(visible), re.M))
    spans = []
    cursor = 0
    for block in content_blocks(text):
        start = text.index(block['text'], cursor)
        cursor = start + len(block['text'])
        spans.append((start, cursor, block['id']))
    def ids(start, end):
        return [bid for a, b, bid in spans if a < end and b > start]
    result = []
    parents = {}
    for index, match in enumerate(matches):
        level = len(match[1])
        parents = {k: v for k, v in parents.items() if k < level}
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        own = text[match.end():end]
        start = match.end() + len(own) - len(own.lstrip())
        own = own.strip()
        # A table/list opener stays visibly structural, never silently skipped.
        paragraph = re.split(r'\n[ \t]*\n', own, maxsplit=1)[0]
        if re.match(r'(?:[-*+]\s|\d+[.)]\s|[|>]|```|~~~)', paragraph):
            paragraph = paragraph.splitlines()[0]
        quote = first_sentence(paragraph)
        result.append({'id': f'heading-{index + 1:04d}', 'level': level,
            'title': match[2].strip(), 'parents': list(parents.values()),
            'heading_block_ids': ids(match.start(), match.end()),
            'opening_block_ids': ids(start, start + len(quote)) if quote else [],
            'opening_quote': quote, 'opening_context': paragraph})
        parents[level] = match[2].strip()
    return result


def output_config(config, sections):
    result = copy.deepcopy(config)
    if not sections:
        return result
    def obj(fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}
    verdict = obj({'status': {'type': 'string', 'enum': ['pass', 'fail']},
                   'reason': {'type': 'string', 'minLength': 1}})
    rows = {s['id']: obj({'heading': copy.deepcopy(verdict), 'opening': copy.deepcopy(verdict),
                          'opening_quote': {'type': 'string', 'enum': [s['opening_quote']]}}) for s in sections}
    root = result['format']['schema']
    root['properties']['heading_answer_reviews'] = obj(rows)
    root['required'].append('heading_answer_reviews')
    return result


def checked(value, text):
    from .content_quality import ContentQualityError
    def invalid():
        raise ContentQualityError('見出しと直下の第一文の個別確認・引用が不足または不整合です。')
    sections = entries(text)
    if not sections:
        return []
    rows = value.get('heading_answer_reviews')
    if not isinstance(rows, dict) or set(rows) != {s['id'] for s in sections}:
        invalid()
    findings = []
    for section in sections:
        row = rows[section['id']]
        if (not isinstance(row, dict) or set(row) != {'heading', 'opening', 'opening_quote'}
                or row['opening_quote'] != section['opening_quote']):
            invalid()
        for name in ('heading', 'opening'):
            verdict = row[name]
            if (not isinstance(verdict, dict) or set(verdict) != {'status', 'reason'}
                    or verdict['status'] not in ('pass', 'fail')
                    or not isinstance(verdict['reason'], str) or not verdict['reason'].strip()):
                invalid()
            # Missing prose cannot be overridden by a model's global pass.
            if name == 'opening' and not section['opening_quote'] and verdict['status'] == 'pass':
                invalid()
            if verdict['status'] != 'fail':
                continue
            locations = section[name + '_block_ids'] if name == 'heading' else section['opening_block_ids']
            findings.append({'check_key': 'prose_quality', 'severity': 'blocking',
                'block_ids': locations or section['heading_block_ids'], 'problem': verdict['reason'],
                'reader_impact': ('見出し「' + section['title'] + '」の意味が曖昧で、対象・行為を誤解し得る。'
                    if name == 'heading' else '見出し「' + section['title'] + '」の直下で具体的な答えを得られない。'),
                'required_change': ('問いの範囲を変えず、対象・行為が明確な見出しへ整える。'
                    if name == 'heading' else '既にある回答と条件を保ち、第一文に具体的な答えを簡潔に示す。全項目を詰め込まず詳説は後に残す。'),
                'stage_to_fix': 'article', 'origin': 'individual_heading_answer',
                'heading_id': section['id'], 'aspect': name})
    return findings
