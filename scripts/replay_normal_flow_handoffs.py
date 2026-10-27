"""Read-only replay of an actual failed article; no provider or database I/O."""
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
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    os.environ.update(SUPABASE_URL='https://offline.invalid',SUPABASE_KEY='offline-test-key',
        SUPABASE_SERVICE_ROLE_KEY='offline-test-key',ANTHROPIC_API_KEY='offline-test-key',OPENAI_API_KEY='offline-test-key',
        ARTICLE_REVIEW_PROVIDER='tiered',QUALITY_RESEARCH_ROUTING='focused')
    original=args.snapshot.read_bytes();saved=json.loads(original)
    artifacts={a['step']:a for a in saved['artifacts']}
    with patch.object(socket.socket,'connect',side_effect=AssertionError('offline replay only')):
        from pipeline import research_requirements as req,generation_context as gc,quality_warnings as warnings
        from pipeline import article_design as ad,upstream_routing as routing,editorial_recovery as recovery,upstream_recovery as controller
        from pipeline.editorial_contract import use
        plan=json.loads(artifacts['research_plan']['content_text'])
        matrix=copy.deepcopy(json.loads(artifacts['research_matrix']['content_text']))
        pages=json.loads(artifacts['fresh_sources']['content_text'])
        # Reapply the deterministic validator to saved provider dispositions and
        # exact saved pages; this does not create a new semantic source verdict.
        req.validate_matrix(matrix,plan,pages);matrix['policy_sha256']=req.matrix_policy();matrix['valid']=False
        before=copy.deepcopy(matrix)
        strict=gc.bundle_data(plan,matrix,quotes=True)
        with patch.object(warnings,'warn'):
            ordinary=warnings.scoped(lambda _:gc.bundle_data(plan,matrix,quotes=True),'article')('offline')
        article=artifacts['article'];outline=artifacts['outline']
        report=json.loads(artifacts['content_audit']['content_text'])
        material=routing.packet(article,outline,report,plan,matrix)
        design=ad.design_context(outline)
        bases=[ad.section_reader_basis(s,design['sections'],design['reader_needs']) for s in design['sections']]
        with patch.object(recovery,'get_optional_artifact',side_effect=lambda j,k:artifacts.get(k)),use(None), \
             patch.object(controller,'run',return_value='normal-v2-controller') as dispatch:
            selected=recovery._run('offline','saved keyword')
        result={'kind':'offline_saved_failure_replay_not_new_article_quality',
            'snapshot_sha256':hashlib.sha256(original).hexdigest(),'provider_calls':0,'database_calls':0,
            'same_supported_evidence_in_initial_and_strict_recovery':ordinary==strict,
            'review_and_question_acceptance_unchanged':matrix==before and not matrix['valid'],
            'published_claims':{d['id']:[c['id'] for c in d.get('claims',[])] for d in strict['decisions']},
            'source_findings_have_real_addresses':bool(material['article_blocks']),
            'reachable_research_question_ids':[q['id'] for q in material['questions']],
            'section_count':len(bases),
            'sections_with_local_feeling':[b['section_id'] for b in bases if b['reader_feeling']],
            'sections_with_causal_motivation':[b['section_id'] for b in bases
                if any(n.get('motivation_context') for n in b['reader_needs'])],
            'saved_normal_assignment_selects_new_controller':selected=='normal-v2-controller' and dispatch.call_count==1,
            'actual_saved_article_quality_passed':report.get('valid') is True,
            'original_snapshot_unchanged':args.snapshot.read_bytes()==original}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='published_claims'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
