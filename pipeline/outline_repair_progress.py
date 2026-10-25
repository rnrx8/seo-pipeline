"""Resume bounded outline edits without resetting progress or accepting old QA."""
import copy
import json

from .content_quality import ContentQualityError, digest

VERSION = 'outline-repair-progress-v1'
MAX_REPAIRS = 3


def findings(report):
    value = {key: report.get(key, []) for key in ('blueprint_issues', 'structural_issues')}
    value['checks'] = [c for c in report.get('checks', []) if c.get('status') == 'fail']
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def history(artifact, load_previous):
    meta = artifact.get('meta') or {}
    saved = meta.get('outline_repair_progress')
    if saved is not None:
        rows = saved.get('history', [])
        if (saved.get('version') != VERSION or not rows or len(rows) > MAX_REPAIRS
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


def prepare(artifact, report, load_previous):
    rows = history(artifact, load_previous)
    if report.get('valid') is not False or report.get('evidence_pending'):
        raise ContentQualityError('修正可能な構成の未合格結果がありません。')
    if len(rows) >= MAX_REPAIRS:
        raise ContentQualityError('構成の局所修正は通算3回を使用しました。保存結果から原因確認が必要です。')
    key = findings(report)
    if any(row.get('findings_sha256') == key for row in rows):
        raise ContentQualityError('同じ構成指摘が繰り返されています。同条件の修正を再送しません。')
    return {'history': rows, 'next': {'round': len(rows) + 1,
            'findings_sha256': key, 'review_snapshot': report.get('snapshot'),
            'before_sha256': digest(artifact['content_text'])}}


def completed(progress, text):
    if progress is None:
        return {}
    row = {**progress['next'], 'after_sha256': digest(text)}
    if row['before_sha256'] == row['after_sha256']:
        raise ContentQualityError('構成の修正で本文が変わっていません。同じ要求を繰り返しません。')
    return {'outline_repair_progress': {'version': VERSION,
            'history': copy.deepcopy(progress['history']) + [row]}}
