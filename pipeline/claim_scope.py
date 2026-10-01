"""Keep literal evidence and explicit conditions together across authoring stages.

This is an index, not a second set of inferred facts. Missing dimensions remain
empty; they must not be interpreted as unrestricted eligibility.
"""
import json
import re

DIMENSIONS = {
    'audience': r'男女とも|男女共|男性|女性|法人|個人|学生|未成年|成人',
    'plan': r'[A-Za-zァ-ヶー]+(?:プラン|コース)|無料会員|有料会員|プレミアム|スタンダード',
    'term': r'\d+\s*(?:ヶ|か|カ)?月|\d+年|月額|年額|一括',
    'tax': r'税込|税別|非課税',
    'campaign': r'キャンペーン|初回|新規|期間限定|通常料金',
    'feature': r'メッセージ(?:送受信|送信|受信|交換)?|会員登録|プロフィール(?:閲覧|登録)?|検索|いいね|基本機能|全機能',
}


def conditional_facts(facts: str) -> list[dict]:
    records = []
    headings = []
    for block in re.split(r'\n\s*\n', facts):
        body = []
        for line in block.splitlines():
            if re.match(r'^#{1,6}\s', line):
                level = len(line)-len(line.lstrip('#'))
                headings = [h for h in headings if len(h)-len(h.lstrip('#')) < level]+[line]
            else:
                body.append(line)
        literal = '\n'.join(body).strip()
        if not literal or not re.search(r'無料|有料|料金|円|プラン|コース', literal):
            continue
        context = '\n'.join(headings)
        records.append({'id': f'condition-{len(records):03d}', 'subject_context': context,
                        'statement': literal,
                        'conditions': {k: list(dict.fromkeys(re.findall(pattern, context+'\n'+literal))) for k,pattern in DIMENSIONS.items()},
                        'values': list(dict.fromkeys(re.findall(r'完全無料|無料|有料|[\d,]+円', literal))),
                        'source_urls': re.findall(r'https?://[^\s<>「」]+', literal)})
    return records


def scope_instructions(facts: str) -> str:
    records = conditional_facts(facts)
    if not records:
        return ''
    return ('\n\n## 対象条件付きの事実索引\n'
            '対象・プラン・期間・機能・値は下の原文を一組として扱う。条件一覧は明示語の索引で、全組合せに値が適用される意味ではない。'
            '空欄は条件なしを意味しない。見出し・冒頭・表・まとめにも必要な条件を残す。'
            '男性のメッセージ有料という事実から、女性を含めた完全無料の不存在を結論しない。\n'
            + json.dumps(records, ensure_ascii=False))


def scope_issues(text: str, facts: str) -> list[dict]:
    """Catch the observed universal free-use claim without a probabilistic verdict.

    Only activate with explicit female-free evidence; scoped headings and quoted
    reader questions are not assertions. Broader conditions remain source-audited.
    """
    records = conditional_facts(facts)
    def female_free(record):
        literal = re.sub(r'[*_>`]', '', record['statement'])
        return bool(re.search(r'女性(?:(?!男性|。|有料).){0,90}?無料(?!では(?:ない|ありません|なく|ございません))', literal))
    evidence = [r for r in records if female_free(r)]
    if not evidence:
        return []
    heading_scope = {}
    issues = []
    for line in text.splitlines():
        match = re.match(r'^(#{1,4})\s+(.+)', line)
        if match:
            level = len(match[1]);heading_scope = {k:v for k,v in heading_scope.items() if k < level}
            heading_scope[level] = match[2]
            continue
        if not line.strip() or line.lstrip().startswith(('>', '|')):
            continue
        for sentence in re.split(r'(?<=[。！？])', line):
            prose = re.sub(r'「[^」]*」|『[^』]*』', '', sentence)
            if not re.search(r'完全無料.{0,65}(?:存在(?:し(?:ない|ません)|せず)|ありません)|無料(?:会員)?で(?:できる|使える|利用できる)(?:のは|範囲は).{0,65}まで(?:です|だ|と|で|$)', prose):
                continue
            if re.search(r'とは(?:限|言え|いえ)|わけでは|という(?:誤解|主張)', prose):
                continue
            audience = set(re.findall(r'男性|女性', prose))
            if not audience:
                # A heading comparing both groups does not restrict a universal claim.
                for heading in reversed(list(heading_scope.values())):
                    inherited = set(re.findall(r'男性|女性', heading))
                    if inherited:
                        audience = inherited
                        break
            if len(audience) == 1 or re.search(r'有料会員|有料プラン', prose):
                continue
            issues.append({'key':'missing_audience_condition','claim':sentence.strip(),
                           'evidence_ids':[r['id'] for r in evidence],
                           'reason':'女性無料の根拠があるため、無料利用の不存在・上限を男女共通の結論にしない。対象・機能を明記する。'})
    return issues
