"""Load an explicitly selected editorial reference for local quality trials.

This does not discover approvals, write artifacts, or promote prose to evidence.
"""
import copy
import hashlib
import json
import re
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


def quote_belongs_to(quote, text):
    """Accept verbatim spans or adjacent Japanese-quoted spans, in source order.

    Reviewers may put presentation brackets around a quotation. Each enclosed
    fragment must still exist exactly in the same case; no prose is rewritten.
    """
    if not isinstance(quote, str) or not quote.strip():
        return False
    if quote in text:
        return True
    if not re.fullmatch(r'(?:「[^「」]+」)+', quote):
        return False
    cursor = 0
    for fragment in re.findall(r'「([^「」]+)」', quote):
        start = text.find(fragment, cursor)
        if start < 0:
            return False
        cursor = start + len(fragment)
    return True


def checked_revision_comparison(keyword, candidate, reference, runs):
    """Bind two order-swapped local comparisons to exact, same-keyword prose.

    A preference is a revision regression signal, never an article QA pass.
    Human labels do not appear in either comparison's model input.
    """
    if (reference.get('keyword') != keyword
            or reference.get('source_status') != 'user_accepted_lead_reference'):
        raise ValueError('Comparison needs an accepted reference for this keyword')
    accepted = reference.get('reference_text')
    if (not isinstance(accepted, str) or not accepted.strip()
            or hashlib.sha256(accepted.encode()).hexdigest() != reference.get('reference_sha256')):
        raise ValueError('Comparison reference text no longer matches its hash')
    if not isinstance(candidate, str) or not candidate.strip() or candidate == accepted:
        raise ValueError('Comparison needs a distinct candidate, not the reference itself')
    if len(runs) != 2:
        raise ValueError('Two order-swapped comparisons are required')
    preferred, reference_positions, reasons = [], [], []
    for run in runs:
        cases, value = run['cases'], run['result']
        if (len(cases) != 2 or len({c['id'] for c in cases}) != 2
                or {c['lead'] for c in cases} != {accepted, candidate}):
            raise ValueError('Comparison cases do not match the frozen prose')
        texts = {c['id']: c['lead'] for c in cases}
        reference_positions.append(next(i for i, c in enumerate(cases) if c['lead'] == accepted))
        rows = value.get('checks', [])
        if (len(rows) != 2 or {r.get('case_id') for r in rows} != set(texts)):
            raise ValueError('Comparison readbacks are missing or duplicated')
        for row in rows:
            spans = row.get('sentence_progression')
            if not isinstance(spans, list) or not spans:
                raise ValueError('Comparison needs actual quoted reader progression')
            for span in spans:
                quote = span.get('quote')
                if not quote_belongs_to(quote, texts[row['case_id']]):
                    raise ValueError('Readback quote does not belong to its source case')
        comparison = value['comparison']
        choice = comparison['preferred_case']
        reason = comparison.get('reason_with_short_quotes')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Comparison needs an editorial reason')
        if choice == 'no_material_preference':
            preferred.append('neither')
        elif choice in texts:
            preferred.append('reference' if texts[choice] == accepted else 'candidate')
        else:
            raise ValueError('Comparison preference points outside its cases')
        reasons.append(reason)
    if reference_positions != [0, 1] and reference_positions != [1, 0]:
        raise ValueError('Reference must be tested in both positions')
    status = ('needs_revision' if preferred == ['reference', 'reference']
              else 'no_regression_detected' if preferred[0] == preferred[1]
              else 'inconclusive')
    return {
        'status': status, 'scope': 'same_keyword_lead_revision_comparison_only',
        'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
        'reference_sha256': reference['reference_sha256'],
        'preferences_by_bound_text': preferred, 'editorial_reasons': reasons,
        'quoted_sources_checked': True, 'both_reference_positions_checked': True,
        'full_article_accepted': False, 'fresh_generation_quality_accepted': False,
        'production_qa_result_changed': False,
    }
