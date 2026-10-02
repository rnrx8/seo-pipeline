"""Deterministic prose-run checks; blank paragraphs do not reset the count."""
import re
from .content_edits import content_blocks

READABILITY_POLICY = '''H2直下・H3・H4を含む本文で、通常の文章が連続300字を超えたら内容に合う表・箇条書き・H4で整理する。
空行・改行・太字だけでは連続の解消としない。比較は表、並列事項はリスト、話題の区切りはH4を使う。
形式のために事実や項目を増やさず、表にした結果の条件欠落・一項目だけの巨大リスト・長文を詰め込んだ表も避ける。
H2/H3や既存H4の削除・移動は禁止。新しいH4は既存H3内の話題整理に限る。'''


def readability_issues(text):
    issues, run, ids = [], [], []
    heading, level, fenced = '', 0, False
    def flush():
        visible = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', ''.join(run))
        visible = re.sub(r'<[^>]*>|[*_`\s]', '', visible)
        if level >= 2 and len(visible) > 300:
            issues.append({'key':'continuous_prose', 'heading':heading, 'characters':len(visible),
                           'reason':'通常の文章が空行を挟んで300字を超えて連続しています。内容に合う形式で整理する。',
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
