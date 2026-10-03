"""Offline request-size scenarios, NOT model verdicts or a completed article.

Archived screen outcomes supply a fixed routing scenario. Unresolved items stay
unresolved; expansion can be forced to price its cost without inventing answers.
The old article supplies volume only, not evidence of normal-flow quality.
"""
import copy
import json
import math
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import tiktoken
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from pipeline import content_quality as cq, focused_quality as fq, tiered_research as tr
from pipeline import focused_research as fr, step_content_audit as ca
from pipeline.quality_budget import RATES
from pipeline.research_requirements import validate_matrix
from pipeline.source_spans import expand_references
from measure_focused_packets import saved_excerpt_policy

MARGIN=1.2
JPY=200


def cost(tokens, output, model, margin=MARGIN):
    n=math.ceil(tokens*margin)
    ir,orr=RATES[model]
    return (n*ir*(2 if n>272000 else 1)+output*orr*(1.5 if n>272000 else 1))/1e6


def summarize(result):
    """Keep the committed report compact; detailed requests remain reproducible."""
    summary={k:v for k,v in result.items() if k!='rows'}
    summary['downstream_rows']=[{k:r[k] for k in ('stage','model','input_local_tokens','input_with_margin','output_cap','cost_usd')}
                                for r in result['rows'] if r['scenario']=='downstream']
    base=result['scenarios']['saved_outcomes_no_expansion']['quality_no_article_repair_usd']
    comparisons=[]
    for stages in (['tiered_article_evidence'],['tiered_article_evidence','tiered_audit_research']):
        selected=[r for r in result['rows'] if r['scenario']=='downstream' and r['stage'] in stages]
        cheaper=base-sum(r['cost_usd'] for r in selected)+sum(cost(r['input_local_tokens'],r['output_cap'],'gpt-6-luna') for r in selected)
        comparisons.append({'hypothetical_luna_stages':stages,'quality_no_repair_usd':cheaper,'quality_validated':False})
    summary['same_input_lower_model_price_only_comparisons']=comparisons
    return summary


def run(research,article):
    enc=tiktoken.get_encoding('o200k_base')
    count=lambda v:len(enc.encode(json.dumps(v,ensure_ascii=False),disallowed_special=()))
    load=lambda path:{p.stem:json.loads(p.read_text()) for p in path.glob('*.json')}
    records,old_article=load(research),load(article)
    get=lambda key:records[key]['content_text']
    plan=json.loads(get('research_plan'));pages=json.loads(get('fresh_sources'))
    job=json.loads((research.parent/'job.json').read_text())
    screens=[];coverage_request=None
    def replay(job_id,step,request):
        nonlocal coverage_request
        if step=='tiered_coverage':
            coverage_request=copy.deepcopy(request)
            return {'coverage_sufficient':False,'coverage_reason':'SIZE ONLY','coverage_issues':[]},NS(input_tokens=0,output_tokens=0)
        saved=records[step]
        exact=saved['meta']['request_sha256']==cq.digest(json.dumps(request,ensure_ascii=False,sort_keys=True))
        if not exact:raise AssertionError('Cannot bind archived source IDs to a different request: '+step)
        raw=json.loads(saved['content_text'])
        if step.startswith('tiered_screen_'):
            payload=json.loads(request['messages'][0]['content'])
            source_index={e['source_ref']:{'url':p['url'],'quote':e['text']} for p in payload['sources'] for e in p['excerpts']}
            value=expand_references(raw,source_index)
            validate_matrix(value,payload['plan'],pages)
            screens.append((step,copy.deepcopy(request),payload,value))
        return copy.deepcopy(raw),NS(input_tokens=0,output_tokens=0)
    prior=saved_excerpt_policy()
    with patch.dict(os.environ,{'QUALITY_RESEARCH_ROUTING':''}), \
         patch.object(cq,'source_evidence',prior),patch.object(tr,'source_evidence',prior), \
         patch.object(tr,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(records.get(s))), \
         patch.object(tr,'checked_request',side_effect=replay):
        reconstructed,_=tr.audit_matrix('offline',plan,pages,get('fact_sheet'),{k:get(k) for k in ('serp','search_intent')})
    assert {i['id'] for i in reconstructed['items']}=={q['id'] for q in plan['items']}

    rows=[]
    def record(stage,request,scenario):
        n=count(request);payload=json.loads(request['messages'][0]['content'])
        item={'stage':stage,'scenario':scenario,'model':request['model'],'input_local_tokens':n,
              'input_with_margin':math.ceil(n*MARGIN),'output_cap':request['max_tokens'],
              'cost_usd':cost(n,request['max_tokens'],request['model']),
              'field_local_tokens':{k:count(v) for k,v in payload.items()},
              'question_ids':[q['id'] for q in payload.get('plan',{}).get('items',[])]}
        item['input_only_usd_without_margin']=cost(n,0,request['model'],margin=1)
        if isinstance(payload.get('requirements'),dict):
            item['requirement_field_local_tokens']={k:count(v) for k,v in payload['requirements'].items()}
        rows.append(item)

    scenarios={}
    for scenario,expand in [('saved_outcomes_no_expansion',False),('saved_outcomes_with_expansion',True)]:
        items=[]
        for step,screen_request,payload,value in screens:
            qs=payload['plan']['items'];ids={q['id'] for q in qs}
            urls={p['url'] for p in payload['sources']}
            selected=[p for p in pages if p['url'] in urls]
            hints=[]
            for key in ('research_matrix_1','research_matrix_2','research_matrix_3'):
                if key in records:hints.extend(i for i in json.loads(get(key)).get('items',[]) if i['id'] in ids)
            packed=tr.packed_sources(selected,hints)
            # Initial screen is a request-size scenario on current excerpt rules.
            # Its outcome is assumed to match the archived screen, not re-judged.
            current_payload=copy.deepcopy(payload)
            current_payload['sources']=tr.indexed_sources(packed)[0]
            current=copy.deepcopy(screen_request)
            current['messages'][0]['content']=json.dumps(current_payload,ensure_ascii=False)
            record(step,current,scenario)
            def capture(job_id,stage,request):
                record(stage,request,scenario)
                body=json.loads(request['messages'][0]['content'])
                # Deliberately return NO semantic approval. This script only
                # exercises request construction and bounded expansion branches.
                result={'items':[{**copy.deepcopy(i),'verified':False,'status':'unresearched','basis':'unresolved',
                                  'answer':'','evidence':[],'reason':'OFFLINE: outcome not evaluated'}
                                 for i in body['candidate_answers']],
                        'coverage_sufficient':False,'coverage_reason':'OFFLINE SIZE ONLY','needs_more_sources':expand}
                return result,NS(input_tokens=0,output_tokens=0)
            with patch.object(tr,'checked_request',side_effect=capture):
                updated,_=fr.review_pending('offline',int(step.rsplit('_',1)[1]),value,qs,
                    {'items':[{k:q[k] for k in ('id','subject','question','priority')} for q in plan['items']],
                     **{k:plan[k] for k in ('candidate_services','scope_reason') if k in plan}},
                    {k:v for k,v in payload['plan'].items() if k!='items'},selected,packed,payload['searches'])
            items.extend(updated['items'])
        scenarios[scenario]={'requests':sum(r['scenario']==scenario for r in rows),
                             'usd':sum(r['cost_usd'] for r in rows if r['scenario']==scenario),
                             'quality_pass':False,'unresolved_after_simulation':sum(not i.get('verified') for i in items)}

    # Use the previously completed article only to exercise current downstream
    # builders at realistic volume. Never write to artifact storage or a ledger.
    req=cq.requirements_for({k:v for k,v in job.items() if k!='id'},job['main_keyword'])
    from pipeline.step_review import SYSTEM_PROMPT
    req.update(research_plan=plan,research_decisions=reconstructed,
               editorial_rules=SYSTEM_PROMPT.split('【チェック・修正項目】',1)[1].split('【出力フォーマット】',1)[0])
    text=old_article['article']['content_text'];outline=old_article['outline']['content_text']
    facts=old_article['fact_sheet']['content_text'];contract=json.loads(old_article['content_contract']['content_text'])
    sources=cq.source_evidence(records['fresh_sources'])
    class Captured(Exception):pass
    def stop_capture(job_id,stage,request):
        record(stage,request,'downstream');raise Captured()
    with patch.object(tr,'checked_request',side_effect=stop_capture):
        try:cq.audit(None,stage='research',text=outline,facts=facts,outline=outline,contract=contract,requirements=req,sources=sources)
        except Captured:pass
    def final_capture(job_id,stage,request):
        record(stage,request,'downstream')
        keys=fq.ROLES[stage.removeprefix('tiered_article_')]
        return {'checks':[{'key':k,'status':'fail','reason':'OFFLINE SIZE ONLY',
                          'affected_blocks':[{'id':fq.content_blocks(text)[0]['id'],'reason':'OFFLINE'}]} for k in keys]},NS(input_tokens=0,output_tokens=0)
    with patch.object(tr,'checked_request',side_effect=final_capture):
        fq.audit_article(None,text=text,facts=facts,outline=outline,contract=contract,requirements=req,sources=sources)
    artifacts={**old_article,'fresh_sources':records['fresh_sources']}
    def failed(*args,**kwargs):
        return {'valid':False,'checks':[{'key':k,'status':'fail' if k=='prose_quality' else 'pass',
                'reason':'OFFLINE SIZE ONLY','affected_blocks':[]} for k in cq.CHECKS]}
    with patch.object(ca,'get_artifact',side_effect=lambda j,s:artifacts[s]), \
         patch.object(ca,'get_job',return_value={}),patch.object(ca,'final_review_requirements',return_value=req), \
         patch.object(ca,'failed_audit_checkpoint',return_value=None),patch.object(ca,'audit',side_effect=failed), \
         patch.object(ca,'upsert_artifact',side_effect=lambda **kw:kw),patch.object(tr,'checked_request',side_effect=stop_capture):
        try:ca.run('offline',job['main_keyword'])
        except Captured:pass
    # The whole-research coverage input exists even when every optional omission
    # is accepted; use its recorded decisions as a separately labeled size fixture.
    record('tiered_coverage',coverage_request,'research_coverage')
    coverage_usd=rows[-1]['cost_usd']
    downstream=[r for r in rows if r['scenario']=='downstream']
    initial=[r for r in downstream if not r['stage'].startswith('tiered_content_repair_')]
    repair=[r for r in downstream if r['stage'].startswith('tiered_content_repair_')]
    final=[r for r in downstream if r['stage'].startswith('tiered_article_')]
    for value in scenarios.values():
        value['quality_no_article_repair_usd']=value['usd']+coverage_usd+sum(r['cost_usd'] for r in initial)
        value['quality_one_repair_and_full_reaudit_usd']=value['quality_no_article_repair_usd']+sum(r['cost_usd'] for r in repair+final)
    return {'api_calls':0,'quality_validated':False,'article_completed':False,'safety_multiplier':MARGIN,'jpy_per_usd':JPY,
            'pricing_basis':'Existing conservative repository constants, not a current provider quote or invoice',
            'scope':'Measured quality stages only. Generation, planning reviews outside these stages, new search, outline repair, encoding retry and any second research round excluded.',
            'fixture_warning':'Archived screen outcomes + current request builders + old article volume. Not a normal-flow completion.',
            'archived_screen_requests_exactly_verified':len(screens),'article_chars':len(text),
            'downstream_matrix_question_count':len(reconstructed['items']),
            'downstream_matrix_matches_current_plan':True,
            'learned_style_rules_missing':True,'whole_research_coverage_fixture_usd':coverage_usd,
            'downstream_no_repair_usd':sum(r['cost_usd'] for r in initial),
            'downstream_input_only_usd_without_margin':sum(r['input_only_usd_without_margin'] for r in initial),
            'downstream_with_one_repair_and_full_reaudit_usd':sum(r['cost_usd'] for r in initial+repair+final),
            'unknown_reread_success_followup_cost_not_included':True,
            'full_article_300_yen_proven':False,'may_authorize_paid_test':False,'scenarios':scenarios,'rows':rows}


if __name__=='__main__':
    with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}), \
         patch.object(socket.socket,'connect',side_effect=AssertionError('No network')), \
         patch.object(socket,'create_connection',side_effect=AssertionError('No network')), \
         patch.object(tr,'create_with_retry',side_effect=AssertionError('No model calls')):
        result=run(Path(sys.argv[1]),Path(sys.argv[2]))
    if len(sys.argv)>4:Path(sys.argv[4]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    summary=summarize(result)
    Path(sys.argv[3]).write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='downstream_rows'},ensure_ascii=False,indent=2))
