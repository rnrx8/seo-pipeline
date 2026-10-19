"""Load an explicitly selected editorial reference for local quality trials.

This does not discover approvals, write artifacts, or promote prose to evidence.
"""
import copy
import hashlib
import json
from pathlib import Path


def load_accepted_reference(path, keyword):
    record = json.loads(Path(path).read_text())
    if record.get('status') != 'user_accepted_lead_reference':
        raise ValueError('The reference has not been accepted by the user')
    if record.get('keyword') != keyword:
        raise ValueError('The reference belongs to a different keyword')
    text = record.get('lead')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('The accepted reference text is missing')
    digest = hashlib.sha256(text.encode()).hexdigest()
    if record.get('lead_sha256') != digest:
        raise ValueError('The accepted reference text no longer matches its hash')
    reasons = record.get('positive_characteristics')
    if (not isinstance(reasons, list) or not reasons
            or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)):
        raise ValueError('The accepted reference needs its editorial reasons')
    scope = record.get('use_scope')
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError('The accepted reference needs its usage scope')
    return {
        'reference_text': text,
        'reference_sha256': digest,
        'keyword': keyword,
        'why_good': copy.deepcopy(reasons),
        'scope': scope,
        'preserve_prefix': False,
        'source_status': record['status'],
        'handling': '今回の部分修正の比較基準。修正対象外の良い流れを保つ。'
                    '新しい明示指示や正しい事実・条件を優先し、他KWの定型文にしない。',
        'evidence_status': '文章への評価であり、事実の検証・記事全体の合格・公開の承認ではない。',
    }


def reference_feedback(feedback, reference):
    if reference is None:
        return feedback
    return feedback + '\n\n【ユーザーが方向性を評価した参照原稿と今回の修正範囲】\n' + json.dumps(reference, ensure_ascii=False)
