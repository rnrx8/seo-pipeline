"""Arithmetic checks for explicitly labelled, period-by-plan Markdown tables.

This is intentionally conservative: it does not infer tax, feature equivalence,
campaign eligibility, or missing prices. Semantic audit handles those conditions.
"""
import re
import unicodedata
from decimal import Decimal


def price_tables(text: str, candidates: list[str]) -> list[dict]:
    tables = []
    text = unicodedata.normalize('NFKC', text)
    candidates = [unicodedata.normalize('NFKC', name) for name in candidates]
    for match in re.finditer(r'(?:^\|[^\n]+\n?)+', text, re.M):
        rows = [[cell.strip() for cell in line.strip().strip('|').split('|')]
                for line in match[0].splitlines()]
        if len(rows) < 3 or rows[0][0] not in ('プラン', 'プラン名'):
            continue
        context = text[:match.start()].strip().splitlines()[-1]
        names = [name for name in candidates if name in context]
        if len(names) != 1:
            continue
        for col, header in enumerate(rows[0][1:], 1):
            period = re.fullmatch(r'(\d+)\s*[ヶケかカ箇]?月(?:プラン)?', header)
            if not period:
                continue
            prices = []
            complete = True
            for row in rows[2:]:
                if col >= len(row):
                    complete = False
                    continue
                # Ignore ranges, discounts and compound alternatives; do not
                # silently turn an indicative "from" price into a fixed price.
                cell = row[col]
                if re.search(r'〜|~|から|割引|OFF|キャンペーン', cell, re.I):
                    complete = False
                    continue
                if int(period[1]) != 1 and not re.search(r'/\s*月', cell):
                    complete = False
                    continue
                amount = re.match(r'([\d,]+)円(?:\s*/\s*月)?(?:\s*[(（][\d,]+円[)）])?$', cell)
                if amount:
                    prices.append({'plan': row[0], 'monthly': int(Decimal(amount[1].replace(',', '')))})
                else:
                    complete = False
            if prices and complete:
                tables.append({'service': names[0], 'months': int(period[1]), 'context': context,
                               'scope': re.findall(r'男性|女性|税込|税別', context),
                               'plans': prices, 'minimum_listed': min(p['monthly'] for p in prices)})
    return tables


def comparison_evidence(text: str, contract: dict) -> tuple[list[dict], list[dict]]:
    candidates = list(dict.fromkeys(name for section in contract.get('required_sections', [])
                                   for name in section.get('candidate_services', [])))
    tables = price_tables(text, candidates)
    issues = []
    normal = unicodedata.normalize('NFKC', text)
    for table in tables:
        peers = [t for t in tables if t['months'] == table['months'] and t['service'] != table['service']
                 and t['scope'] == table['scope']]
        if not peers:
            continue
        # A long-term *general* conclusion must not silently switch to a more
        # expensive plan. Explicitly scoped plan comparisons remain legitimate.
        longest = max(t['months'] for t in tables if t['service'] == table['service'])
        period = rf'{table["months"]}\s*[ヶケかカ箇]?月'
        if table['months'] == longest and longest >= 6:
            period = rf'(?:{period}|長期)'
        pattern = rf'{period}[^。！？、\n]{{0,45}}{re.escape(table["service"])}[^。！？、\n]{{0,20}}(?:割安|安い|最安)'
        for match in re.finditer(pattern, normal):
            # Explicit plan/feature qualifications are left to semantic audit.
            sentence = re.split(r'[。！？\n]', normal[:match.start()])[-1] + match[0]
            plan_names = [p['plan'] for t in [table, *peers] for p in t['plans']]
            if any(p in sentence for p in plan_names) or re.search(r'条件|機能|に限|の場合|を使うなら', sentence):
                continue
            cheaper = [t for t in peers if t['minimum_listed'] < table['minimum_listed']]
            if cheaper:
                issues.append({'key': 'unqualified_price_conclusion', 'excerpt': match[0],
                               'months': table['months'], 'claimed_cheaper': table['service'],
                               'minimum_listed': table['minimum_listed'],
                               'lower_priced_alternatives': [{'service': t['service'], 'monthly': t['minimum_listed']}
                                                            for t in cheaper],
                               'reason': '期間内の最安値と一般的な安さの結論が一致しません。機能条件を確認し結論を限定または訂正する。'})
    return tables, issues
