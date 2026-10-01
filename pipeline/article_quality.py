"""Deterministic delivery checks shared by writing, editing and final validation."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


@dataclass(frozen=True)
class LengthBudget:
    target: int
    minimum: int
    maximum: int


def parse_length_budget(setting: str | None) -> LengthBudget | None:
    value = unicodedata.normalize('NFKC', setting or '')
    if not value or value.startswith('競合'):
        return None
    numbers = [int(n.replace(',', '')) for n in re.findall(r'\d[\d,]*', value)]
    if not numbers or not numbers[0]:
        return None
    lo = numbers[0]
    hi = numbers[1] if len(numbers) > 1 else lo
    if hi < lo:
        raise ValueError('文字数の上限が目標・下限より小さくなっています。')
    target = lo if '上限' in value else (lo + hi) // 2
    # Catch severe omissions without encouraging padding to an exact count.
    return LengthBudget(target, int(lo * .8), int(hi * 1.1))


def normalize_heading(title: str) -> str:
    title = unicodedata.normalize('NFKC', title)
    title = re.sub(r'^まとめ[|｜:：\s]+(?=\S)', '', title)
    return re.sub(r'[\W_]+', '', title).lower()


def heading_matches(a: str, b: str) -> bool:
    a, b = normalize_heading(a), normalize_heading(b)
    if not a or not b:
        return False
    return a == b or SequenceMatcher(None, a, b).ratio() >= .88


def normalize_outline_headings(text: str) -> str:
    """Canonicalize explicit H2/H3/H4 labels; never promote editorial labels."""
    pattern = re.compile(r'^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?H([234])(?:[-−]?\d+)?[ \t]*[.．:：｜|][ \t]*(.+?)(?:\*\*)?[ \t]*$')
    lines = []
    fenced = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(('```','~~~')):
            fenced = not fenced
        match = pattern.match(line.rstrip('\r\n')) if not fenced else None
        if match:
            level = int(match[1])
            ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            line = '#' * (level + 1) + f' H{level}：{match[2].strip()}' + ending
        lines.append(line)
    return ''.join(lines)


def outline_sections(text: str) -> list[dict]:
    """Read the explicit H2/H3/H4 labels, not editorial metadata headings."""
    matches = list(re.finditer(r'^#{2,5}\s+H([234])(?:[-−]?\d+)?\s*[.．:：｜|]\s*(.+)$', text, re.M))
    result = []
    parent = None
    for i, match in enumerate(matches):
        level, title = int(match[1]), match[2].strip()
        if level == 2:
            parent = title
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result.append({'level': level, 'title': title, 'parent': parent,
                       'start': match.start(), 'end': end})
    return result


def select_outline(text: str, titles: list[str]) -> str:
    sections = outline_sections(text)
    return '\n'.join(text[s['start']:s['end']] for s in sections
                     if any(heading_matches(s['parent'] or '', t) for t in titles))


def article_sections(text: str) -> list[dict]:
    matches = list(re.finditer(r'^(#{2,4})\s+(.+)$', text, re.M))
    parent = None
    result = []
    for i, match in enumerate(matches):
        level, title = len(match[1]), match[2].strip()
        if level == 2:
            parent = title
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result.append({'level': level, 'title': title, 'parent': parent,
                       'body': text[match.end():end].strip()})
    return result


def delivery_heading_matches(item, expected, contract=None):
    if heading_matches(item['title'], expected['title']):
        return True
    names = {normalize_heading(n) for section in (contract or {}).get('required_sections', [])
             for n in section.get('candidate_services', [])}
    if item['level'] == 3:
        left = re.split(r'[|｜]', item['title'], maxsplit=1)
        right = re.split(r'[|｜]', expected['title'], maxsplit=1)
        return (len(left) == len(right) == 2 and normalize_heading(left[0]) in names
                and normalize_heading(left[0]) == normalize_heading(right[0]))
    return False


def validate_delivery(text: str, outline: str, setting: str | None = None, contract: dict | None = None, section_map: dict | None = None) -> list[dict]:
    issues = validate_promised_comparison_count(text)
    if not outline_sections(outline):
        issues.append({'key': 'missing_outline', 'reason': '完成確認に必要な構成がありません'})
    actual = article_sections(text)
    from .section_identity import valid_binding
    binding_ok = section_map is not None and valid_binding(text, outline, section_map)
    if section_map is not None and not binding_ok:
        issues.append({'key': 'stale_section_identity'})
    used = set()
    for expected_index, expected in enumerate(outline_sections(outline)):
        found = next((i for i, item in enumerate(actual) if i not in used
                      and item['level'] == expected['level']
                      and ((binding_ok and section_map['entries'][i]['outline_index'] == expected_index)
                           or (not binding_ok and delivery_heading_matches(item, expected, contract)
                               and (expected['level'] == 2 or heading_matches(item['parent'] or '', expected['parent'] or ''))))), None)
        if found is None:
            issues.append({'key': 'missing_heading', 'level': expected['level'], 'title': expected['title']})
        else:
            used.add(found)
            body = re.sub(r'https?://\S+|[\s*#>|_\-]', '', actual[found]['body'])
            minimum = 1 if expected['level'] == 2 else 40
            if len(body) < minimum:
                issues.append({'key': 'empty_section', 'title': expected['title']})
    internal = re.search(
        r'【PART\d+_END】|\[hypothesis\]|'
        r'(?:以下|次|上記|この|本)(?:の)?H[234](?:で|では|に)(?:各|紹介|説明|解説|扱)|'
        r'(?:前の?パート|次のパート|前パート|次パート).{0,160}(?:想定|執筆|指定に従|未完|省略)|'
        r'(?:指定に従い|続きは次|以下省略|ここに.{0,20}(?:記載|挿入))', text, re.I)
    if internal:
        issues.append({'key': 'internal_note', 'excerpt': internal[0][:180]})
    for line in text.splitlines():
        if line.lstrip().startswith('|') and re.search(r'要確認|未確認|確認中|\bTBD\b', line, re.I):
            issues.append({'key': 'unfinished_table', 'excerpt': line[:180]})
    budget = parse_length_budget(setting)
    if budget and len(text) < budget.minimum:
        issues.append({'key': 'article_too_short', 'actual': len(text), 'minimum': budget.minimum,
                       'reason': '構成の説明不足を補完する。水増しや未確認情報の追加は禁止'})
    return issues


def validate_promised_comparison_count(text: str) -> list[dict]:
    """Check explicit N-selection promises against named comparison tables.

    Only recognized service/entity tables count, not tips, plan rows, or incidental
    mentions. Semantic audit handles aliases, transposed tables and evidence depth.
    """
    headings = list(re.finditer(r'^(#{1,2})\s+(.+)$', text, re.M))
    issues = []
    for index, heading in enumerate(headings):
        promised = re.search(r'(\d+)選', unicodedata.normalize('NFKC', heading[2]))
        if not promised:
            continue
        end = len(text)
        if len(heading[1]) == 2:
            end = next((h.start() for h in headings[index + 1:]), len(text))
        names = set()
        named_table = False
        for line in text[heading.end():end].splitlines():
            if not line.lstrip().startswith('|'):
                named_table = False
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            name = re.sub(r'[*_`]', '', cells[0]).strip()
            if name in ('サービス名', 'アプリ名', '会社名', '企業名', 'サービス', 'アプリ'):
                named_table = True
                continue
            if named_table and name and not set(name) <= {'-', ':', ' '}:
                names.add(unicodedata.normalize('NFKC', name).casefold())
        if names and len(names) != int(promised[1]):
            issue = {'key': 'comparison_count_mismatch', 'promised': int(promised[1]),
                     'table_count': len(names), 'title': heading[2]}
            issues.append(issue)
    return issues
