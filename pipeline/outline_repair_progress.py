"""Resume bounded outline edits without resetting progress or accepting old QA."""
import copy
import json

from .content_quality import ContentQualityError, digest

VERSION = 'outline-repair-progress-v2-obligations'
MAX_REPAIRS = 3


def findings(report):
    value = {key: report.get(key, []) for key in ('blueprint_issues', 'structural_issues')}
    value['checks'] = [c for c in report.get('checks', []) if c.get('status') == 'fail']
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def obligations(report, artifact):
    """Stable existing requirements/sections, independent of model phrasing."""
    ids=set()
    for issue in report.get('blueprint_issues', []):
        if issue.get('decision_id'):
            ids.add('decision:'+issue['decision_id']+':'+str(issue['requirement_index']))
        elif issue.get('section_id'):
            ids.add('section:'+issue['section_id']+':'+str(issue['requirement_index']))
    from .article_quality import outline_sections
    from .content_edits import content_blocks
    text=artifact['content_text'];sections=(artifact.get('meta') or {}).get('editorial_blueprint',{}).get('sections',[])
    spans=outline_sections(text);blocks={};cursor=0
    for block in content_blocks(text):
        start=text.index(block['text'],cursor);cursor=start+len(block['text']);blocks[block['id']]=(start,cursor)
    for check in report.get('checks',[]):
        if check.get('status')!='fail':continue
        localized=set()
        for loc in check.get('affected_blocks',[]):
            start,end=blocks.get(loc['id'],(-1,-1))
            localized.update(s['id'] for span,s in zip(spans,sections) if start<span['end'] and end>span['start'])
        ids.update('check:'+check['key']+':'+sid for sid in localized)
        if not localized and (check.get('key')!='coverage' or not report.get('blueprint_issues')):
            ids.add('check:'+check.get('key','unknown'))
    if report.get('structural_issues'):ids.add('structure')
    return sorted(ids)


def history(artifact, load_previous):
    meta = artifact.get('meta') or {}
    saved = meta.get('outline_repair_progress')
    if saved is not None:
        rows = saved.get('history', [])
        if (saved.get('version') not in (VERSION,'outline-repair-progress-v1') or not rows
                or any(row.get('round') != n for n, row in enumerate(rows, 1))
                or any(a.get('after_sha256') != b.get('before_sha256') for a, b in zip(rows, rows[1:]))
                or rows[-1].get('after_sha256') != digest(artifact['content_text'])):
            raise ContentQualityError('構成修正の保存履歴と現在の版が一致しません。再送しません。')
        return copy.deepcopy(rows)
    if not meta.get('local_repaired'):
        return []
    # Legacy outlines already consumed at least one repair. Do not grant a new
    # first attempt or claim that a missing historic finding was reconstructed.
    previous = load_previous()
    before, after = digest(previous['content_text']), digest(artifact['content_text'])
    if before == after:
        raise ContentQualityError('既存の構成修正の変更を確認できません。再送しません。')
    return [{'round': 1, 'before_sha256': before, 'after_sha256': after,
             'findings_sha256': None, 'legacy_repair_counted': True}]


def prepare(artifact, report, load_previous, load_review=None):
    rows = history(artifact, load_previous)
    if report.get('valid') is not False or report.get('evidence_pending'):
        raise ContentQualityError('修正可能な構成の未合格結果がありません。')
    from .quality_budget import spending_limits_enabled
    if spending_limits_enabled() and len(rows) >= MAX_REPAIRS:
        raise ContentQualityError('構成の局所修正は通算3回を使用しました。保存結果から原因確認が必要です。')
    # Upgrade older records only from exact archived reports and before-text.
    # Unreconstructable legacy rounds conservatively consume each obligation.
    for row in rows:
        if 'obligation_ids' not in row and row.get('review_snapshot') and load_review:
            old,before=load_review(row['round'])
            if old.get('snapshot')!=row['review_snapshot'] or digest(before['content_text'])!=row['before_sha256']:
                raise ContentQualityError('構成修正の過去指摘と保存版が一致しません。回数を再付与しません。')
            row['obligation_ids']=obligations(old,before)
    targets=obligations(report,artifact)
    if not targets:raise ContentQualityError('構成修正の具体的な対象がありません。')
    exhausted=[key for key in targets if sum('obligation_ids' not in row or key in row['obligation_ids'] for row in rows)>=MAX_REPAIRS]
    if exhausted:
        raise ContentQualityError('同じ回答要件の修正が3回続いています。新しい指摘と区別して原因確認が必要です: '+', '.join(exhausted))
    key = findings(report)
    if any(row.get('findings_sha256') == key for row in rows):
        raise ContentQualityError('同じ構成指摘が繰り返されています。同条件の修正を再送しません。')
    return {'history': rows, 'next': {'round': len(rows) + 1,
            'findings_sha256': key, 'review_snapshot': report.get('snapshot'), 'obligation_ids':targets,
            'before_sha256': digest(artifact['content_text'])}}


def completed(progress, text):
    if progress is None:
        return {}
    row = {**progress['next'], 'after_sha256': digest(text)}
    if row['before_sha256'] == row['after_sha256']:
        raise ContentQualityError('構成の修正で本文が変わっていません。同じ要求を繰り返しません。')
    if any(row['after_sha256'] in (old['before_sha256'],old['after_sha256']) for old in progress['history']):
        raise ContentQualityError('構成修正が過去の版へ循環しています。再送しません。')
    return {'outline_repair_progress': {'version': VERSION,
            'history': copy.deepcopy(progress['history']) + [row]}}
