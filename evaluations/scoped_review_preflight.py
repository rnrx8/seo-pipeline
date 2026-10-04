"""Build real saved-outline requests offline; never execute the saved run harness.

The review verdicts below are scripted scope scenarios, NOT a quality verdict.
Only sizes/IDs/cost ceilings are exported; original artifacts are read-only.
"""
import argparse
import copy
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import content_quality as cq, step_research_guard as guard, tiered_evidence as evidence
from pipeline.content_edits import content_blocks
from pipeline.focused_quality import ROLES
from pipeline.quality_context import review_requirements
from pipeline.quality_budget import RATES, REVIEW_PHASE_LIMITS


def run(saved_run):
    root=Path(saved_run)
    load=lambda name:json.loads((root/'staged'/f'{name}.json').read_text())
    job=json.loads((root/'job.json').read_text())
    text=load('outline')['content_text']
    blocks=content_blocks(text)
    requirements={k:job.get(k) for k in ('custom_prompt','must_include','company_restriction','word_count_setting','article_purpose','target_audience','tone_style','citation_style','service_id','cta_id')}
    requirements.update(keyword=job['main_keyword'],research_plan=json.loads(load('research_plan')['content_text']),research_decisions=json.loads(load('research_matrix')['content_text']))
    sources=cq.source_evidence(load('fresh_sources'))
    facts=cq.confirmed_facts(load('fact_sheet')['content_text'])
    contract=json.loads(load('content_contract')['content_text'])
    captured=[]
    class Captured(Exception):pass
    def record(job,step,request):
        data=json.loads(request['messages'][0]['content'])
        body={'model':request['model'],'instructions':request['system'],'input':request['messages'],
              'max_output_tokens':request['max_tokens'],'reasoning':{'effort':'medium'},'store':False,'stream':True,
              'text':{'format':request['output_config']['format']},'service_tier':'default'}
        size=len(json.dumps(body,ensure_ascii=False).encode())+4096
        # Local proxy only; o200k is not asserted to be the provider's tokenizer.
        import tiktoken
        local_tokens=len(tiktoken.get_encoding('o200k_base').encode(json.dumps(body,ensure_ascii=False),disallowed_special=()))
        ir,orr=RATES[request['model']]
        maximum=(size*ir*(2 if size>272000 else 1)+request['max_tokens']*orr*(1.5 if size>272000 else 1))/1e6
        captured.append({'operation':step,'model':request['model'],'json_input_bytes_with_slack':size,
            'local_o200k_proxy_tokens':local_tokens,'output_token_limit':request['max_tokens'],'conservative_request_ceiling_yen_at_200':round(maximum*200,2),
            'shown_blocks':len(data.get('article_blocks',data.get('outline_blocks',[]))),
            'target_block_ids':data.get('target_block_ids',[]),
            'source_count':len(data.get('source_documents',[])),
            'source_characters':sum(len(p['text']) for p in data.get('source_documents',[]) if isinstance(p,dict)),
            'research_items':len(data.get('requirements',{}).get('research_decisions',{}).get('items',[]))})
        return data
    with patch('pipeline.tiered_research.checked_request',side_effect=lambda *a: (record(*a),(_ for _ in ()).throw(Captured()))):
        try:cq.audit(None,stage='research',text=text,facts=facts,outline=text,contract=contract,requirements=requirements,sources=sources)
        except Captured:pass
    # Reproduce the previously unresolved female messaging question. This is
    # declared scenario input, not a fresh model decision about the now-passed outline.
    q='q76'
    locations=[{'id':b['id'],'reason':'女性の無料メッセージ条件（再現シナリオ）'} for b in blocks if b['id'] in ('block-0032','block-0074')]
    check={'key':'evidence_support','status':'fail','reason':'標準プランの無料範囲を照合する再現シナリオ',
           'affected_blocks':locations,'requires_source_check':True,'research_ids':[q],'source_urls':[]}
    def send(job,step,request):
        record(job,step,request)
        return {'checks':[{'key':k,'status':'pass','reason':'offline scripted response; no quality judgment','affected_blocks':[]} for k in ROLES['evidence']],
                'review':{'needed':step.endswith('screen'),'block_ids':[locations[0]['id']] if step.endswith('screen') else [],
                'source_urls':['https://kikon-match.co.jp/price','https://kikon-match.co.jp/faq'],'reason':'offline scripted escalation'}}, SimpleNamespace(input_tokens=0,output_tokens=0)
    with patch('pipeline.tiered_research.checked_request',side_effect=send):
        evidence.evidence_audit('research',{'article_blocks':blocks,'requirements':review_requirements(requirements,'research'),
            'source_documents':sources,'candidate_checks':[check]},cq.AUDIT_SYSTEM,ROLES['evidence'],text)
    with patch.object(guard,'get_artifact',side_effect=lambda j,s:load(s)),patch.object(guard,'get_job',return_value=job), \
         patch.object(guard,'requirements_for',return_value=requirements),patch('pipeline.research_requirements.require_matrix',return_value={}), \
         patch('pipeline.tiered_research.checked_request',side_effect=lambda *a:(record(*a),(_ for _ in ()).throw(Captured()))):
        try:guard.repair_outline(job['id'],job['main_keyword'],{'checks':[check]})
        except Captured:pass
    return {'kind':'offline_request_construction_not_model_validation','paid_calls':0,'source_job_id':job['id'],
        'scope':'Saved research/outline only; article not yet generated. No full completion cost forecast.',
        'source_pages_before':len(json.loads(sources)),'source_chars_before':sum(len(p['text']) for p in json.loads(sources)),
        'calls':captured,'phase_limits_usd':REVIEW_PHASE_LIMITS,
        'warning':'Ceilings use UTF-8 bytes as a conservative token bound. They are not expected charges. Output uncertainty and actual article requests remain untested.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--saved-run',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}), \
         patch.object(socket.socket,'connect',side_effect=AssertionError('offline preflight: network forbidden')), \
         patch.object(socket,'create_connection',side_effect=AssertionError('offline preflight: network forbidden')):
        result=run(args.saved_run)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
