"""Actionable editorial findings inside the existing article reviewer calls."""
import copy

VERSION = 'editorial-findings-v1'
POLICY = '''【修正必須と任意改善の区別】
今回のeditorial_findingsは担当checkごとの具体的な指摘。severity=blockingは、主回答・必要な判断材料の欠落、誤り、誤解、意味が通らない文章など公開前に直す欠陥。severity=improvementは、現状でも読者の問い・判断が成立する任意改善。モデルの好みだけによる語尾変更、短い共感、感情の強弱、正当なマーケティング誘導だけでblockingにしない。ユーザーが明示した媒体規則・禁止表現は維持する。感情や次の問いを毎章へ追加する義務はない。理解が進み潜在課題へ気づく構成は許可する。架空の不安・効果・緊急性、判断に必要な制約の隠蔽は欠陥として扱う。
各指摘にcheck_key、severity、実在のblock_ids、problem（不足する回答・説明または問題）、reader_impact（理解・判断・行動にどう影響するか）、required_change（必要な修正）、stage_to_fixを記す。stage_to_fixは資料不足ならresearch、順序・回答範囲ならoutline、本文の説明・表現ならarticle、表示形式ならpresentation、入力だけで原因工程を確定できなければinvestigate。現在の記事にない原因を推測しない。根拠の再収集と文章の説明不足を区別する。
checksのfailとaffected_blocksにはblockingだけを対応させ、improvementはchecksをfailにしない。passのaffected_blocksは空配列。blockingのblock_idsの集合と同じcheckのaffected_blocksのID集合を一致させる。欠落は補うべき既存段落を指定する。問題がなければeditorial_findingsは空配列でよい。任意改善を必ず見つける必要はない。'''
STAGES = ('research', 'outline', 'article', 'presentation', 'investigate')


def output_config(config, keys):
    result = copy.deepcopy(config)
    root = result['format']['schema']
    properties = {
        'check_key': {'type': 'string', 'enum': list(keys)},
        'severity': {'type': 'string', 'enum': ['blocking', 'improvement']},
        'block_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
        **{k: {'type': 'string', 'minLength': 1} for k in
           ('problem', 'reader_impact', 'required_change')},
        'stage_to_fix': {'type': 'string', 'enum': list(STAGES)},
    }
    root['properties']['editorial_findings'] = {'type': 'array', 'items': {
        'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}}
    root['required'].append('editorial_findings')
    return result


def checked_findings(value, checks, text):
    from .content_quality import ContentQualityError
    from .content_edits import content_blocks
    def fail():
        raise ContentQualityError('編集指摘の重要度・読者への影響・修正箇所が判定と一致しません。')
    findings = value.get('editorial_findings')
    if not isinstance(findings, list): fail()
    valid_ids = {b['id'] for b in content_blocks(text)}
    by_key = {c['key']: c for c in checks}
    for finding in findings:
        if (not isinstance(finding, dict) or finding.get('check_key') not in by_key
                or finding.get('severity') not in ('blocking', 'improvement')
                or finding.get('stage_to_fix') not in STAGES
                or any(not isinstance(finding.get(k), str) or not finding[k].strip()
                       for k in ('problem', 'reader_impact', 'required_change'))): fail()
        ids = finding.get('block_ids')
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or i not in valid_ids for i in ids)
                or len(ids) != len(set(ids))): fail()
    for key, check in by_key.items():
        locations = {i for f in findings if f['check_key'] == key and f['severity'] == 'blocking'
                     for i in f['block_ids']}
        if ((check['status'] == 'fail') != bool(locations)
                or locations != {l['id'] for l in check['affected_blocks']}): fail()
    return copy.deepcopy(findings)


def decision_findings(value, design):
    """Keep a required answer missing even when the broad coverage verdict passes."""
    result = []
    for decision in design['reader_decisions']:
        row = value['decision_reviews'][decision['id']]
        if row['status'] != 'fail' or decision['role'] == 'supporting': continue
        result.append({'check_key': 'coverage', 'severity': 'blocking',
            'block_ids': list(dict.fromkeys(l['id'] for l in row['affected_blocks'])),
            'problem': row['reason'],
            'missing_answers': [decision['answer_requirements'][i] for i in row['missing_requirements']],
            'reader_impact': '「' + decision['question'] + '」に必要な回答が不足している。',
            'required_change': '不足する必要回答を、採用した根拠と適用条件の範囲で説明する。',
            # A missing answer alone cannot establish which upstream step caused it.
            'stage_to_fix': 'investigate', 'decision_id': decision['id'],
            'origin': 'individual_decision_review'})
    if 'lead_direction' in design:
        from .lead_review import LABELS
        for key,row in value.get('lead_review',{}).items():
            if row['status']!='fail':continue
            result.append({'check_key':'coverage','severity':'blocking',
                'block_ids':list(dict.fromkeys(loc['id'] for loc in row['affected_blocks'])),
                'problem':row['reason'],'reader_impact':LABELS[key]+'が成立せず、この記事を読む意味を判断しにくい。',
                'required_change':'本文の実際の回答に沿って'+LABELS[key]+'を明確にする。固定の共感文や無関係な情報は追加しない。',
                'stage_to_fix':'article','origin':'individual_lead_review','lead_check':key})
    return result
