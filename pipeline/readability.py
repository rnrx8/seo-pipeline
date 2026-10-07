"""Deterministic prose-run checks; blank paragraphs do not reset the count."""
import re
from .content_edits import content_blocks

EMPATHY_ENDING_POLICY = '''「ですよね」は1記事で2回までとし、語尾を単調に反復しない。
「〜はずです」「〜ではないでしょうか」「〜が大切です」「〜をおすすめします」などを文脈に合わせて使う。
自然に締まる場合は共感表現を省略してよい。アカウント独自の学習済み文体ルールがある場合はそちらを優先する。'''

READABILITY_POLICY = '''H2直下・H3・H4を含む本文では、通常の文章の連続を300字以内に収める。超過したら、まず既出情報の反復・不要な免責・冗長な言い回しを整理する。読者の判断に必要な事実・条件や、望みと強みを結ぶ説明・有用な共感は残す。
必要な内容が長く残る場合は、その内容に合う形式で整理する。比較は表、同じ観点の並列事項や手順はリスト、独立した話題の区切りはH4を使う。異なる役割の段落へ箇条書き記号を付けるだけで整理済みとしない。
空行・改行・太字だけでは連続の解消としない。形式のために事実や項目を増やさず、条件欠落・一項目だけの巨大リスト・長文を詰め込んだ表も避ける。修正後も300字以内と内容に合う情報整理の両方を満たす。
H2/H3や既存H4の削除・移動は禁止。新しいH4は既存H3内の話題整理に限る。'''


def readability_issues(text):
    issues, run, ids = [], [], []
    heading, level, fenced = '', 0, False
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
