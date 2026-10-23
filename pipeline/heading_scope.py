"""Candidate handoff: record each child's role in its actual parent's answer."""
import copy

POLICY = '''heading_scope_groupsは親見出しが答える問いと、実際の子節の対応です。
heading_scope_reviewsで各子節の役割を個別に確認する。relationは、親の問いへの答えそのものならanswer、その答えの選択・適用に必要な条件や除外ならqualification、親の答えを理解した後の別の課題・次の判断ならnext_decision。reasonに親の約束する回答と子節の内容の関係を具体的に示す。
同じテーマに関係することや元の構成に含まれていることだけでanswer/qualificationにしない。複数の方法・候補を挙げる親の下では、各方法・候補の説明と、方法を決めた後の準備・利用手順という別の判断を区別する。一方、対象外の候補を除く説明や、その方法を選ぶために必要な条件はqualificationになり得る。商品名の存在や短い補足だけを理由に章を分けない。
next_decisionは親子の回答範囲を構成側で直すべき指摘として扱う。既存のorganizationの担当範囲を個別に確認する記録であり、すべての節への感情文・新しい章・新たな調査を要求するものではない。'''


def groups(text, outline, design):
    from .article_design import correspondence
    mapped = {s['blueprint_id']: s for s in correspondence(text, outline, design)['sections']}
    result = []
    for parent in design['sections']:
        children = [s for s in design['sections'] if s['parent_id'] == parent['id']]
        if not children:
            continue
        result.append({'id': parent['id'], 'title': parent['title'], 'question': parent['question'],
                       'children': [{'id': s['id'], 'title': s['title'], 'question': s['question'],
                                     'block_ids': mapped[s['id']]['block_ids']} for s in children]})
    return result


def output_config(config, scopes):
    result = copy.deepcopy(config)
    if not scopes:
        return result
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    row = obj({'relation': {'type': 'string', 'enum': ['answer', 'qualification', 'next_decision']},
               'reason': {'type': 'string', 'minLength': 1}})
    schema = obj({p['id']: obj({c['id']: copy.deepcopy(row) for c in p['children']}) for p in scopes})
    result['format']['schema']['properties']['heading_scope_reviews'] = schema
    result['format']['schema']['required'].append('heading_scope_reviews')
    return result


def checked(value, scopes, text):
    from .content_quality import ContentQualityError
    from .content_edits import content_blocks
    if not scopes:
        return []
    def fail():
        raise ContentQualityError('親見出しと子節の回答範囲の個別確認が不足・不整合です。')
    rows = value.get('heading_scope_reviews')
    if not isinstance(rows, dict) or set(rows) != {p['id'] for p in scopes}:
        fail()
    valid = {b['id'] for b in content_blocks(text)}
    findings = []
    for parent in scopes:
        children = rows[parent['id']]
        if not isinstance(children, dict) or set(children) != {c['id'] for c in parent['children']}:
            fail()
        for child in parent['children']:
            row = children[child['id']]
            if (not isinstance(row, dict) or set(row) != {'relation', 'reason'}
                    or row['relation'] not in ('answer', 'qualification', 'next_decision')
                    or not isinstance(row['reason'], str) or not row['reason'].strip()):
                fail()
            if row['relation'] != 'next_decision':
                continue
            ids = child['block_ids']
            if not ids or any(i not in valid for i in ids):
                fail()
            findings.append({'check_key': 'prose_quality', 'severity': 'blocking',
                'block_ids': list(dict.fromkeys(ids)), 'problem': row['reason'],
                'reader_impact': '親の問い「' + parent['question'] + '」と別の判断が同階層で混在する。',
                'required_change': '子節「' + child['title'] + '」の問いを担う位置・階層へ構成を修正し、必要な回答と前後の接続を保持する。',
                'stage_to_fix': 'outline', 'origin': 'individual_heading_scope'})
    return findings


def merge(checks, findings):
    result = copy.deepcopy(checks)
    if not findings:
        return result
    check = next(c for c in result if c['key'] == 'prose_quality')
    check['status'] = 'fail'
    for finding in findings:
        check['reason'] += '\n' + finding['problem']
        existing = {loc['id'] for loc in check['affected_blocks']}
        check['affected_blocks'] += [{'id': i, 'reason': finding['problem']} for i in finding['block_ids'] if i not in existing]
    return result

