"""Deterministic prose-run checks; blank paragraphs do not reset the count."""
import re
from .content_edits import content_blocks

SENTENCE_REVIEW_LIMIT = 80

SENTENCE_STYLE_POLICY = '一文は60字程度を目安に意味が伝わる自然な長さにする。80字超は確認候補であり自動不合格ではない。条件・主述・感情の意味を保ち、長さだけで機械的に分割しない。'

PROSE_STYLE_POLICY = '''記事の説明はです・ます調を基本にし、ユーザーが指定した文体を優先する。
詳しい人が読者の事情をわかったうえで、普段の言葉で直接説明する距離感にする。リード・比較・手順・商品紹介・注意点・まとめを通じて、読者が何を見て、どう考え、何をすればよいか、それが自分にどう役立つかを自然に理解できる言葉で伝える。
抽象的な行為名や書き手側の評価だけで説明を進めず、読者が実際にすることや思い浮かべられる場面へ結びつける。専門用語や必要な分類は意味と精度を保って使い、言い換えるとかえって曖昧になる箇所は短く説明を添える。語を一律に禁止・置換しない。結論・推薦と、その判断に必要な事実・数値・否定・対象条件は明確に残す。
親しみは具体的な言葉と説明の自然な流れで出す。共感の決まり文句、くだけた語尾、会話の演出を足すことに頼らず、全てを「〜しましょう」に揃えない。感情を扱わない章にも同じ距離感を保ち、定義や条件への回答だけで足りる箇所に共感を増やさない。
見出し・表・リストに加え、読者自身の短い実感や望みを表す部分は、視点と文脈が自然なら常体・体言止めを使える。その部分だけを文体違反として説明調へ一律に直さない。記事全体を不揃いな口調へ変える指示ではない。この文体は読者向け本文の基準であり、原文引用・数値表・根拠や分析の内部記録を口語へ書き換える指示ではない。'''

EMPATHY_ENDING_POLICY = '語尾は文脈とアカウントの文体ルールに合わせる。「ですよね」「はずです」等への変換や章末の共感は必須ではない。全員の気持ちを決めつけず、必要な共感・期待・納得は章の説明で実現し、自然に締まる場合は補完文を追加しない。'

READABILITY_POLICY = '''H2直下・H3・H4を含む本文では、通常の文章の連続を300字以内に収める。超過したら、まず既出情報の反復・不要な免責・冗長な言い回しを整理する。読者の判断に必要な事実・条件や、望みと強みを結ぶ説明・有用な共感は残す。
必要な内容が長く残る場合は、その内容に合う形式で整理する。比較は表、同じ観点の並列事項や手順はリスト、独立した話題の区切りはH4を使う。異なる役割の段落へ箇条書き記号を付けるだけで整理済みとしない。
空行・改行・太字だけでは連続の解消としない。形式のために事実や項目を増やさず、条件欠落・一項目だけの巨大リスト・長文を詰め込んだ表も避ける。修正後も300字以内と内容に合う情報整理の両方を満たす。
本文生成では構成にある見出しを使い、H4も新設しない。H2/H3や既存H4の削除・移動は禁止。局所修正工程で明示的に許可された新しいH4だけは、既存H3内の話題整理に限る。'''


def readability_issues(text):
    issues, run, ids = [], [], []
    heading, level, fenced, quoted = '', 0, False, False
    def flush():
        visible = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', ''.join(run))
        visible = re.sub(r'<[^>]*>|[*_`\s]', '', visible)
        if level >= 2 and len(visible) > 300:
            issues.append({'key':'continuous_prose', 'heading':heading, 'characters':len(visible),
                           'reason':'通常の文章が空行を挟んで300字を超えて連続しています。必要な内容を保持して重複・冗長さを整理し、必要なら内容に合う形式を選んで連続300字以内にする。',
                           'affected_blocks':[{'id':i,'reason':'連続文章の整理対象'} for i in dict.fromkeys(ids)]})
        run.clear(); ids.clear()
    for block in content_blocks(text):
        lines = block['text'].splitlines()
        in_table = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(('```', '~~~')):
                flush(); fenced = not fenced; continue
            if fenced: continue
            if re.fullmatch(r'(?:-{3,}|\*{3,}|_{3,})',stripped):continue
            is_quote=stripped.startswith('>')
            if stripped and is_quote!=quoted:
                flush();quoted=is_quote
            match = re.match(r'^(#{1,6})\s+(.+)', stripped)
            if match:
                flush(); level, heading = len(match[1]), match[2]; continue
            if not stripped: continue
            next_line = lines[index + 1].strip() if index + 1 < len(lines) else ''
            table_header = '|' in stripped and bool(re.match(r'^\|?\s*:?-{3,}:?\s*\|(?:\s*:?-{3,}:?\s*\|?)+$', next_line))
            if table_header or (in_table and '|' in stripped):
                in_table = True; flush(); continue
            in_table = False
            if re.match(r'^(?:[-*+]\s|\d+[.)]\s|!\[)', stripped):
                flush(); continue
            stripped = re.sub(r'^>\s?', '', stripped)
            # HTML decoration and comments cannot be used to evade prose checks.
            clean = re.sub(r'<[^>]*>', '', stripped)
            if clean: run.append(clean); ids.append(block['id'])
    flush()
    return issues


def long_sentence_candidates(text, limit=SENTENCE_REVIEW_LIMIT):
    """Review hints, never automatic failures: style/empathy exceptions need context.

    Count visible Japanese prose, excluding headings, table/list rows and code.
    Markdown destinations and HTML markup do not contribute to the count.
    """
    from html import unescape
    candidates = []
    fenced = False
    for block in content_blocks(text):
        prose = []
        for line in block['text'].splitlines():
            stripped = line.strip()
            if stripped.startswith(('```', '~~~')):
                fenced = not fenced
                continue
            if fenced or re.match(r'^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|!\[|\|)', stripped):
                continue
            # A Markdown table without leading pipes is still a table.
            if re.search(r'(?m)^\s*\|?\s*:?-{3,}:?\s*\|', block['text']):
                continue
            prose.append(re.sub(r'^>\s?', '', stripped))
        visible = '\n'.join(prose)
        visible = re.sub(r'<!--.*?-->', '', visible, flags=re.S)
        visible = re.sub(r'!?\[([^\]]*)\]\([^\n]*?\)', r'\1', visible)
        visible = unescape(re.sub(r'<[^>]*>', '', visible))
        visible = re.sub(r'[*_`]', '', visible)
        for sentence in re.findall(r'[^。！？!?]+[。！？!?]*', visible):
            sentence = sentence.strip()
            characters = len(re.sub(r'\s', '', sentence))
            if characters > limit:
                candidates.append({'id': block['id'], 'sentence': sentence, 'characters': characters})
    return candidates


def h3_volume_regressions(before, after, cta_plan=None):
    """Protect an adequate standalone H3 from becoming thin during editing.

    Existing thin chapters remain semantic-review findings. Table/list contents
    count; formatting, code, destinations and the injected CTA do not. H4
    children retain the agreed exception to the H3 introduction minimum.
    """
    from html import unescape
    def lengths(text):
        if cta_plan and cta_plan.get('button_text') and cta_plan.get('url'):
            button='['+cta_plan['button_text']+']('+cta_plan['url']+')'
            pattern=r'(?m)^---[ \t]*\n(?:>[^\n]*\n)+---[ \t]*(?:\n|$)'
            text=re.sub(pattern,lambda m: '' if button in m[0] else m[0],text)
        sections=[]; current=None; fenced=False
        for line in text.splitlines():
            stripped=line.strip()
            if stripped.startswith(('```','~~~')):
                fenced=not fenced;continue
            if fenced:continue
            heading=re.match(r'^(#{1,6})\s+(.+)',stripped)
            if heading:
                level=len(heading[1])
                if level<=3:
                    if current is not None:sections.append(current)
                    current={'heading':heading[2],'lines':[],'h4':False} if level==3 else None
                elif level==4 and current is not None:current['h4']=True
                continue
            if current is not None:
                if not stripped or re.fullmatch(r'[|:\-\s]+',stripped):continue
                visible=re.sub(r'!\[[^\]]*\]\([^)]*\)','',stripped)
                visible=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',visible)
                visible=re.sub(r'<[^>]*>','',visible)
                visible=re.sub(r'^(?:>\s*|[-*+]\s+|\d+[.)]\s+)','',visible)
                current['lines'].append(re.sub(r'[|*_`\s]','',unescape(visible)))
        if current is not None:sections.append(current)
        return [{**s,'characters':len(''.join(s['lines']))} for s in sections]
    old,new=lengths(before),lengths(after)
    # Heading identities are validated by the existing section mapping gate.
    return [{'key':'h3_volume_regression','heading':n['heading'],
             'before_characters':o['characters'],'characters':n['characters'],
             'reason':'修正で配下H4のないH3の説明量が200字未満になっています。表・リストも含む節全体の必要な説明を保ってください。'}
            for o,n in zip(old,new) if o['heading']==n['heading'] and not o['h4']
            and not n['h4'] and o['characters']>=200>n['characters']]
