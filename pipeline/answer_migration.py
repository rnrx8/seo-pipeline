"""Lossless, reviewable partition of legacy answers; never certifies research.

A reviewer supplies semantic boundaries. No keyword rules infer which text is
safe to remove, and dates/conditions are not reconstructed from substrings.
"""
import copy
import hashlib
import json

ROLES = {'answer', 'usage_condition', 'research_record'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def migration_candidate(matrix, partitions, *, expected_fingerprint):
    """Return a sidecar, leaving evidence and original research approval intact.

    Each partition covers the complete original answer in its original order.
    Unchanged answers are retained verbatim. This output is NOT a research matrix
    and cannot be passed to generation_evidence as an approved replacement.
    """
    if fingerprint(matrix) != expected_fingerprint:
        raise ValueError('調査原本が変更されています。移行案を再確認してください。')
    ids = [item['id'] for item in matrix['items']]
    if len(ids) != len(set(ids)) or set(partitions) - set(ids):
        raise ValueError('回答IDが重複、または移行対象が原本にありません。')
    records = []
    for item in matrix['items']:
        original = item['answer']
        segments = copy.deepcopy(partitions.get(item['id'], [{'role':'answer','text':original}]))
        if not segments or any(set(s) != {'role','text'} or s['role'] not in ROLES or not isinstance(s['text'],str) for s in segments):
            raise ValueError('移行区分が不正です。')
        if ''.join(s['text'] for s in segments) != original:
            raise ValueError('原文の欠落・追加・並び替えはできません。')
        records.append({'id':item['id'], 'segments':segments,
                        'evidence':copy.deepcopy(item.get('evidence',[])),
                        'supports_current_conclusion':item.get('supports_current_conclusion'),
                        'basis':item.get('basis'), 'status':item.get('status'),
                        'publishable':item.get('basis') != 'omitted' and item.get('status') in ('confirmed','explicitly_undisclosed')})
    return {'kind':'legacy_answer_partition_candidate', 'source_sha256':expected_fingerprint,
            'source_policy_sha256':matrix.get('policy_sha256'), 'requires_semantic_review':True,
            'production_approved':False, 'records':records}
