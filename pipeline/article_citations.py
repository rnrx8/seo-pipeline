"""Article-wide source identity; models select sources, Python owns numbering.

This validates reference identity/format only. Semantic QA still checks whether a
source supports the claim; a matching URL never supplies that verdict.
"""
import copy
import hashlib
import json
import re

VERSION = 'article-citations-v1'
TOKEN = re.compile(r'\[\[cite:([A-Za-z0-9_-]+)\]\]')
DEFINITION = re.compile(r'^※(\d+)[:：]\s*(https?://\S+?)(?:\s*[（(]確認日[:：]?[^）)]+[）)])?\s*$')
POLICY = '''出典はcitation_registryのsource_idsにあるIDを[[cite:ref1]]の形で主張の直後に指定する。
同じURLは同じ出典。章・パートごとの採番、末尾出典一覧の執筆、出典の「ほか」による省略はしない。番号と一覧はプログラムが生成する。
既存原稿の※Nは同じcitation_registryの番号として保持できる。新たな出典は対応IDで指定し、既存番号の意味を変えない。
出典表示の指定がない場合は出典記号を追加しない。内部IDは本文の説明には書かない。'''


def fail(message):
    from .content_quality import ContentQualityError
    raise ContentQualityError('出典対応: ' + message)


def _lines(text):
    fenced = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(('```','~~~')):
            fenced = not fenced
            yield line,False
        else:
            yield line,not fenced


def _visible(text):
    return ''.join(line for line,visible in _lines(text) if visible)


def build(sources, existing=None):
    """Preserve existing URL numbers; consolidate multiple excerpt IDs per URL."""
    rows = copy.deepcopy(existing.get('entries', [])) if existing else []
    numbers = {r['url']: r['number'] for r in rows}
    if len(numbers) != len(rows) or len({r['number'] for r in rows}) != len(rows):
        fail('出典一覧のURLまたは番号が重複しています。')
    for ref, value in sources.items():
        url = value.get('url', '')
        if not isinstance(url, str) or not re.fullmatch(r'https?://[^\s<>]+', url):
            fail('確認済み資料のURLを特定できません。')
        if url not in numbers:
            numbers[url] = max(numbers.values(), default=0) + 1
            rows.append({'number': numbers[url], 'url': url})
    result = {'version': VERSION, 'entries': rows,
              'source_ids': {ref: numbers[value['url']] for ref, value in sources.items()}}
    result['sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def from_context(evidence, existing=None):
    start = evidence.find('{')
    try:
        bundle = json.loads(evidence[start:])
        return build(bundle['sources'], existing)
    except (ValueError, KeyError, TypeError) as exc:
        fail('執筆資料の出典集合を読み取れません。' + str(exc))


def for_article(article, evidence):
    existing = (article.get('meta') or {}).get('citation_registry')
    if existing is None:
        # A legacy article's explicit definitions are the only safe numerical
        # identity. Never infer a missing number from section topic or order.
        definitions = definitions_in(article['content_text'])
        existing = {'entries': [{'number': n, 'url': u} for n, u in sorted(definitions.items())]}
    registry = from_context(evidence, existing)
    allowed = {v['url'] for v in json.loads(evidence[evidence.find('{'):])['sources'].values()}
    if any(row['url'] not in allowed for row in registry['entries']):
        fail('原稿にある出典を確認済み資料と対応できません。')
    return registry


def definitions_in(text):
    result = {}
    for line,visible in _lines(text):
        if not visible:continue
        match = DEFINITION.fullmatch(line.strip())
        if match:
            n, url = int(match[1]), match[2]
            if n in result and result[n] != url:
                fail('同じ番号が異なるURLを指しています。')
            result[n] = url
    return result


def tokens(text, registry):
    def replace(match):
        if match[1] not in registry['source_ids']:
            fail('確認済み資料にない出典IDです: ' + match[1])
        return '※' + str(registry['source_ids'][match[1]])
    return ''.join(TOKEN.sub(replace,line) if visible else line for line,visible in _lines(text))


def render(text, registry, *, footer=True):
    """Idempotent rendering; never renumber by a model's part-local definitions."""
    text = tokens(text, registry)
    known = {r['number']: r['url'] for r in registry['entries']}
    lines = []
    for line,visible in _lines(text):
        match = DEFINITION.fullmatch(line.strip()) if visible else None
        if match:
            if known.get(int(match[1])) != match[2]:
                fail('原稿の出典定義と記事全体の出典が一致しません。')
            if footer:
                continue
        lines.append(line)
    body = ''.join(lines).rstrip()
    visible_body = _visible(body)
    used = {int(n) for n in re.findall(r'※(\d+)(?!\d)', visible_body)}
    if used - set(known):
        fail('対応する出典がない番号です: ' + ','.join(map(str, sorted(used-set(known)))))
    if '[[cite:' in visible_body:
        fail('出典IDの指定が未完成です。')
    if footer and used:
        body += '\n\n' + '\n'.join(f'※{n}: {known[n]}' for n in sorted(used)) + '\n'
    return body


def integrity_issues(text, registry=None):
    """Cheap final/pre-QA check, without asserting source support."""
    try:
        definitions = definitions_in(text)
        body = '\n'.join(line for line,visible in _lines(text) if visible and not DEFINITION.fullmatch(line.strip()))
        used = {int(n) for n in re.findall(r'※(\d+)', body)}
        if used - set(definitions):
            fail('本文から参照する番号の出典定義がありません。')
        if '[[cite:' in body:
            fail('表示前の出典IDが残っています。')
        if registry:
            known = {r['number']:r['url'] for r in registry['entries']}
            if any(known.get(n) != url for n,url in definitions.items()):
                fail('出典定義が保存したURL対応と異なります。')
    except ValueError as exc:
        return [{'key':'citation_identity','reason':str(exc)}]
    return []
