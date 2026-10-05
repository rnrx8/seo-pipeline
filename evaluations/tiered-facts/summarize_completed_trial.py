"""Read-only accounting and prompt-size checks. Never invokes a provider or DB."""
import argparse
import collections
import copy
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def summarize(directory):
    from pipeline import step_article
    import tiktoken
    enc = tiktoken.get_encoding('o200k_base')
    count = lambda s: len(enc.encode(s, disallowed_special=()))
    ledger = json.loads(next((directory / 'budget').glob('*.json')).read_text())
    calls = ledger['calls']
    groups = collections.defaultdict(lambda: {'accounted_calls': 0, 'accounted_jpy': 0})
    roles = collections.defaultdict(list)
    duplicate_jpy = 0
    superseded_jpy = 0
    for c in calls:
        role = (c.get('review_operation') or {}).get('role', c['stage'])
        if c['status'] != 'accounted':
            continue
        cost = c['cost_usd'] * 200
        groups[role]['accounted_calls'] += 1
        groups[role]['accounted_jpy'] += cost
        if (c.get('review_operation') or {}).get('phase') == 'article':
            roles[role].append(cost)
        if c.get('duplicate_review_reconciliation'):
            superseded_jpy += cost
            repeated = calls[c['duplicate_review_reconciliation']['retained_call_index']]
            if repeated['status'] == 'accounted':
                duplicate_jpy += repeated['cost_usd'] * 200
    audit_roles = ['evidence_scope', 'coverage', 'language', 'consistency', 'organization']
    last_round = sum(roles[r][-1] for r in audit_roles)
    max_round = sum(max(roles[r]) for r in audit_roles)
    preliminary = sum(c.get('cost_usd', 0) * 200 for c in calls
                      if c['status'] == 'accounted' and c['stage'] == 'research_validation')
    # This is an illustrative recombination of prior billed calls, NOT a clean-run
    # measurement, provider quote, or ceiling for another keyword/new research.
    sample_scenarios = {
        'saved_outline_check_plus_two_latest_article_audits_plus_largest_repair_jpy': preliminary + 2 * last_round + max(roles['repair']),
        'saved_outline_check_plus_two_max_article_audits_plus_two_largest_repairs_jpy': preliminary + 2 * max_round + sum(sorted(roles['repair'], reverse=True)[:2]),
    }
    artifact = lambda s: json.loads((directory / 'staged' / (s + '.json')).read_text())['content_text']
    outline = artifact('outline')
    sections = step_article._split_sections_into_parts(step_article._parse_volume_design(outline))
    parts = []
    class Captured(Exception): pass
    for number, assigned in enumerate(sections, 1):
        recorded = []
        def capture(client, messages, **kwargs):
            recorded.append(copy.deepcopy(messages))
            raise Captured()
        # Measure the exact removed duplicate, not an estimated LLM summary.
        full_message = '## 構成案\n' + outline + '\n担当章を執筆してください。'
        with patch.object(step_article, '_call', side_effect=capture):
            try:
                step_article._write_complete_part(None, [{'role':'user', 'content':full_message}],
                    1000, outline, assigned, number)
            except Captured:
                pass
        assert len(recorded) == 1
        sent = recorded[0][0]['content']
        assert sent.count(outline) == 1
        subset = step_article.select_outline(outline, [s[0] for s in assigned])
        before_instruction = ('\n【このパートの完成条件】以下の構成のH2/H3/H4をすべて本文まで書き切る。'
            '見出しは表記を維持し、次パートに持ち越さない。'
            '内部メモ・執筆予定・要確認の比較表は禁止。根拠は提供された確認済み要約または直接取得本文。要約の欠落を非公表扱いにしない。\n' + subset)
        parts.append({'part': number, 'target_characters': sum(s[2] for s in assigned),
            'draft_characters': len(artifact(f'article_part_{number}_attempt_1')),
            'duplicate_outline_tokens_removed_net_of_new_instruction': count(full_message + before_instruction) - count(sent),
            'complete_outline_preserved': True})
    return {'kind': 'historical_accounting_and_offline_prompt_checks', 'api_calls': 0,
        'exchange_rate_jpy_per_usd': 200,
        'accounted_jpy': sum(c['cost_usd']*200 for c in calls if c['status']=='accounted'),
        'unknown_reserved_jpy': sum(c['reserved_usd']*200 for c in calls if c['status']=='unknown_cost_reserved'),
        'role_totals': dict(groups), 'known_calendar_repeat_jpy': duplicate_jpy,
        'superseded_original_calendar_calls_jpy': superseded_jpy,
        'latest_complete_five_role_round_jpy': last_round,
        'max_per_role_recombined_round_jpy': max_round,
        'historical_cost_scenarios_not_new_article_quotes': sample_scenarios,
        'new_research_search_intent_and_outline_generation_excluded': True,
        'normal_run_300_jpy_quality_target_verified': False,
        'writing_parts': parts, 'tokenizer': 'local o200k_base; not provider billing',
        'quality_from_changed_prompts_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('trial_directory', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('offline only')), \
         patch.object(socket, 'create_connection', side_effect=AssertionError('offline only')):
        result = summarize(args.trial_directory)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
