"""Replay saved public-source reviews offline, without generation or database I/O."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root',type=Path,default=root)
    parser.add_argument('--fixture',type=Path,default=root/'tests/fixtures/confirmed-evidence-handoff-20261027.json')
    args=parser.parse_args()
    sys.path.insert(0,str(args.code_root.resolve()))
    os.environ.update(SUPABASE_URL='https://offline.invalid',SUPABASE_KEY='offline-test-key',
        SUPABASE_SERVICE_ROLE_KEY='offline-test-key',ANTHROPIC_API_KEY='offline-test-key',
        OPENAI_API_KEY='offline-test-key',ARTICLE_REVIEW_PROVIDER='tiered',QUALITY_RESEARCH_ROUTING='focused')
    cases=json.loads(args.fixture.read_text())['cases']
    result={'fixture_sha256':hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            'provider_calls':0,'database_calls':0,'cases':[]}
    with patch.object(socket.socket,'connect',side_effect=AssertionError('offline replay only')):
        from pipeline import research_requirements as req, generation_context as gc, quality_warnings as warnings
        from pipeline.source_spans import expand_references
        for case in cases:
            value=expand_references(copy.deepcopy(case['raw_response']),case['source_index'])
            plan={'items':[case['question']]}
            gaps=req.validate_matrix(value,plan,case['pages'])
            value.update(valid=False,attempt=3,policy_sha256=req.matrix_policy())
            before=copy.deepcopy(value)
            with patch.object(warnings,'warn'):
                bundle=warnings.scoped(lambda _:gc.bundle_data(plan,value,quotes=True),'outline')('offline-replay')
            facts=req.accepted_facts(value,plan)
            original=value['items'][0]
            published=[r for d in bundle['decisions'] for r in d['claims']]
            result['cases'].append({'question_id':original['id'],'original_response_step':case['step'],
                'original_request_sha256':case['request_sha256'],
                'unresolved_question_preserved':bool(gaps) and not original['verified'] and not value['valid'],
                'publication_operations':[r['operation'] for r in published],
                'publication_claim_ids':[r['id'] for r in published],
                'publication_conditions':{r['id']:r['conditions'] for r in published},
                'publication_source_urls':{r['id']:[bundle['sources'][ref]['url'] for ref in r['source_ids']] for r in published},
                'withheld_text_absent':all(r['text'] not in facts for r in original['claims'] if r['disposition'] in ('withhold','omit')),
                'stored_matrix_unchanged':value==before,'fact_text_chars':len(facts)})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
