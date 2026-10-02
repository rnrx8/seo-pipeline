"""No-network replay of captured evidence and collection control, not model quality.

Usage: python evaluations/tiered-facts/replay_simplified.py STAGED_DIRECTORY OUTPUT
No .env load, no external SDK requests, no production persistence.
"""
import ast
import collections
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pipeline import research_collection as collection
from pipeline import research_requirements as requirements
from pipeline.fresh_sources import FreshSources


def run(staged):
    records = {p.stem:json.loads(p.read_text()) for p in staged.glob('*.json')}
    content = lambda key:json.loads(records[key]['content_text'])
    plan, pages, matrix = content('research_plan'), content('fresh_sources'), content('research_matrix_1')
    # Compare old and new mechanical acceptance on the exact same captured data.
    old_source = subprocess.check_output(['git','show','239253d:pipeline/research_requirements.py'],cwd=ROOT,text=True)
    old_fn = next(n for n in ast.parse(old_source).body if isinstance(n,ast.FunctionDef) and n.name=='validate_matrix')
    namespace = dict(vars(requirements))
    exec(compile(ast.Module(body=[old_fn],type_ignores=[]),'<baseline-validator>','exec'),namespace)
    previous = namespace['validate_matrix'](copy.deepcopy(matrix),plan,copy.deepcopy(pages))
    current = requirements.validate_matrix(copy.deepcopy(matrix),plan,copy.deepcopy(pages))
    assert previous == current, 'Mechanical evidence acceptance changed'
    original_ids = {g['id'] for g in current}
    # A fabricated quote must remain rejected for an otherwise accepted answer.
    tampered = copy.deepcopy(matrix)
    target = next(i for i in tampered['items'] if i['id'] not in original_ids and i.get('evidence') and i.get('basis')!='omitted')
    target['evidence'][0]['quote'] = 'THIS QUOTE WAS NEVER IN THE CAPTURED SOURCE'
    rejected = requirements.validate_matrix(tampered,plan,copy.deepcopy(pages))
    assert target['id'] in {g['id'] for g in rejected}

    fresh = FreshSources({},[])
    fresh.pages = {p['url']:copy.deepcopy(p) for p in pages}
    requests = []
    def save(**kw):records[kw['step']] = copy.deepcopy(kw);return kw
    def replay_collect(client, **kw):
        marker='\n## 今回の調査担当（この対象と質問だけを調べる）\n'
        task=json.JSONDecoder().raw_decode(kw['prompt'].split(marker,1)[1])[0]
        requests.append({'subject':task['subject'],'question_ids':[q['id'] for q in task['items']],
            'initial_payload_bytes':len((kw['system']+kw['prompt']+kw['context_override']).encode()),
            'max_rounds':kw['max_rounds'],'search_max_uses':kw['search_tool']['max_uses']})
        # No invented findings: replay the old notes as unapproved raw material.
        notes='\n\n'.join(r['content_text'] for key,r in snapshots.items()
            if key.startswith('research_collection_') and r.get('meta',{}).get('subject')==task['subject'])
        return NS(usage=NS(input_tokens=0,output_tokens=0)),notes,[],[]
    snapshots=copy.deepcopy(records)
    with patch.object(collection,'get_optional_artifact',side_effect=lambda j,s:records.get(s)), \
         patch.object(collection,'upsert_artifact',side_effect=save),patch.object(fresh,'save'), \
         patch.object(fresh,'fetch_confirmed_citations'),patch.object(collection,'run_with_fetch',side_effect=replay_collect):
        args=dict(plan=plan,fresh=fresh,system='captured evidence replay',prompt='same day',model='claude-sonnet-4-6',search_tool={})
        collection.collect('offline',None,**args,gaps=json.dumps(current,ensure_ascii=False))
        first_count=len(requests)
        collection.collect('offline',None,**args,gaps=json.dumps(current,ensure_ascii=False))
        assert len(requests)==first_count, 'Completed retrieval repeated on resume'
        assert {i for r in requests for i in r['question_ids']}==original_ids
        active=collection.collection_records('offline')
        assert len(active)==len({q['subject'] for q in plan['items']})

    by_subject=collections.Counter(q['subject'] for q in plan['items'])
    return {'status':'offline_control_checks_passed','external_api_calls':0,'new_article_generated':False,
        'semantic_quality_revalidated':False,'plan_questions':len(plan['items']),
        'old_collection_batches':sum((n+7)//8 for n in by_subject.values()),'new_collection_batches':len(by_subject),
        'old_per_collection_request_ceiling':sum((n+7)//8 for n in by_subject.values())*8,
        'new_per_collection_request_ceiling':len(by_subject)*3,
        'captured_unresolved_items':len(current),'captured_gap_ids':sorted(original_ids),
        'mechanical_acceptance_unchanged':True,'fabricated_quote_rejected':True,
        'supplement_subjects':first_count,'repeated_completed_collection_calls':len(requests)-first_count,
        'collection_requests':requests,
        'cost_status':'No live token usage. Initial payload bytes and theoretical request ceilings are not a yen estimate.'}


if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network is forbidden in offline replay')), \
         patch.object(socket,'create_connection',side_effect=AssertionError('Network is forbidden in offline replay')):
        result=run(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='collection_requests'},ensure_ascii=False,indent=2))
