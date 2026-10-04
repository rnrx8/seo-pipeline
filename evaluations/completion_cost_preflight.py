"""Offline cost scenarios, not a quote of actual future token usage."""
import argparse
import json
import math
import os
import socket
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch


def estimate(root):
    import tiktoken
    from pipeline import content_quality as cq, focused_quality as fq
    from pipeline.step_article import _parse_volume_design, _split_sections_into_parts, _calc_part_max_tokens
    from evaluations.scoped_review_preflight import run as outline_preflight
    enc=tiktoken.get_encoding('o200k_base')
    root=Path(root)
    artifact=lambda s:json.loads((root/'staged'/f'{s}.json').read_text())
    job=json.loads((root/'job.json').read_text())
    outline=artifact('outline')['content_text']
    facts=cq.confirmed_facts(artifact('fact_sheet')['content_text'])
    contract=json.loads(artifact('content_contract')['content_text'])
    sources=cq.source_evidence(artifact('fresh_sources'))
    requirements={k:job.get(k) for k in ('custom_prompt','must_include','company_restriction','word_count_setting','article_purpose','target_audience','tone_style','citation_style','service_id','cta_id')}
    requirements.update(keyword=job['main_keyword'],research_plan=json.loads(artifact('research_plan')['content_text']),research_decisions=json.loads(artifact('research_matrix')['content_text']))
    initial=outline_preflight(root)['calls']
    rows=[]
    # Request construction uses placeholder text, replaced in the estimate with
    # a 2-token-per-character allowance for the saved article length ceiling.
    # No placeholder is saved as an article or treated as a quality result.
    placeholder='# 記事見積用\n\n## 本文\n\n未生成の本文のための入力サイズ見積。'
    from pipeline.article_quality import parse_length_budget
    body_allowance=math.ceil(parse_length_budget(job['word_count_setting']).maximum*2)
    def capture(j,step,request):
        data=json.loads(request['messages'][0]['content'])
        stripped={**data,'article_blocks':[]}
        measured={**request,'messages':[{'role':'user','content':json.dumps(stripped,ensure_ascii=False)}]}
        tokens=len(enc.encode(json.dumps(measured,ensure_ascii=False),disallowed_special=()))+body_allowance
        ir,orr=(.125,.5) if request['model']=='gpt-6-luna' else (2.5,10.)
        low=(tokens*ir+1000*orr)/1e6*200
        high=(math.ceil(tokens*1.2)*ir+request['max_tokens']*orr)/1e6*200
        rows.append({'operation':step,'model':request['model'],'proxy_input_tokens_with_body_allowance':tokens,
                     'scenario_yen_low':round(low,2),'scenario_yen_high':round(high,2)})
        fields=request['output_config']['format']['schema']['properties']['checks']['items']['properties']
        return {'checks':[{'key':k,'status':'pass','reason':'offline sizing fixture','affected_blocks':[],
            **({'requires_source_check':False,'source_urls':[],'research_ids':[]} if 'requires_source_check' in fields else {})} for k in fields['key']['enum']]},NS(input_tokens=0,output_tokens=0)
    with patch('pipeline.tiered_research.checked_request',side_effect=capture):
        fq.audit_article(None,text=placeholder,facts=facts,outline=outline,contract=contract,requirements=requirements,sources=sources)
    first=initial[0]
    outline_low=(first['local_o200k_proxy_tokens']*2.5+1000*10)/1e6*200
    outline_high=(first['local_o200k_proxy_tokens']*1.2*2.5+first['output_token_limit']*10)/1e6*200
    ledger_path=next((root/'budget').glob('*.json'));ledger=json.loads(ledger_path.read_text())
    count_key='5c33d3c71e0b3d6b56457077fcde1b9214e4d2e8f1fe87b3907df5bc79ec8dca'
    first_bound=ledger['input_counts'][count_key]['input_token_bound']
    assert first_bound==153109, 'Historical counting baseline changed; inspect before estimating'
    # The historical bound includes 10% + 4096 slack. It belongs to the previous
    # exact request, not a newly measured prompt. Settings must be frozen before execution.
    base_lower=math.floor((first_bound-4096)/1.1)
    sections=_split_sections_into_parts(_parse_volume_design(outline))
    generation=[];prior_chars=0;prior_output_limits=0
    for i,part in enumerate(sections):
        chars=sum(p[2] for p in part)
        output_max=_calc_part_max_tokens(job['word_count_setting'],chars)+8000
        input_low=base_lower+math.ceil(prior_chars*1.5)
        input_high=first_bound+prior_output_limits+i*8192
        # Standard uncached input/output rates used by the current ledger.
        # Cache hits are NOT assumed. Cache-write/price changes are a limitation.
        low=(input_low*4+math.ceil(chars*1.5)*20)/1e6*200
        high=(input_high*4+output_max*20)/1e6*200
        generation.append({'part':i+1,'planned_characters':chars,'output_limit_including_reasoning':output_max,
            'scenario_input_low':input_low,'scenario_input_high':input_high,
            'scenario_yen_low':round(low,2),'scenario_yen_high':round(high,2)})
        prior_chars+=chars;prior_output_limits+=output_max
    service=next(c for c in reversed(ledger['calls']) if c.get('stage')=='service_map' and c.get('status')=='accounted')['cost_usd']*200
    quality=[outline_low+sum(r['scenario_yen_low'] for r in rows),outline_high+sum(r['scenario_yen_high'] for r in rows)]
    writer=[sum(r['scenario_yen_low'] for r in generation)+service,sum(r['scenario_yen_high'] for r in generation)+service]
    # One outline repair, its full recheck, plus a scoped source adjudication.
    extras={r['operation']:r for r in initial[1:]}
    outline_repair_high=outline_high+sum((r['local_o200k_proxy_tokens']*1.2*(.125 if r['model']=='gpt-6-luna' else 2.5)+r['output_token_limit']*(.5 if r['model']=='gpt-6-luna' else 10))/1e6*200 for r in extras.values())
    return {'kind':'offline_scenarios_not_provider_quote','paid_calls':0,'fx_assumption_jpy_per_usd':200,
        'scope':'saved research and outline -> current outline validation -> service map -> 3 writing parts -> CTA -> 5 final roles -> deterministic final checks',
        'outline_check_yen':[round(outline_low,2),round(outline_high,2)],'final_role_requests':rows,
        'writing_parts':generation,'service_map_prior_observed_yen':round(service,2),
        'normal_without_repair_yen':{'writing_and_service':[round(x,2) for x in writer],'quality':[round(x,2) for x in quality],
                                    'combined':[round(writer[i]+quality[i],2) for i in (0,1)]},
        'incremental_scenarios':{'outline_repair_recheck_and_scoped_sources_high_yen':round(outline_repair_high,2),
                                 'one_writing_part_regeneration_high_yen':round(max(p['scenario_yen_high'] for p in generation)+20,2),
                                 'another_five_role_check_high_yen':round(sum(r['scenario_yen_high'] for r in rows),2)},
        'proposed_limits_yen':{'total':1000,'generation_including_service':750,'quality':250,'outline_quality':100,'article_quality':'250 minus actual outline quality spend'},
        'limitations':['No completed article or real model response is represented by these scenarios.',
                       'Generation baseline comes from the previous counted request; latest service/CTA/settings have not been remeasured.',
                       'Input estimates are a local o200k proxy, not provider billing tokens. Text/settings/source revisions can change them.',
                       'Reasoning, repairs, truncation and part regeneration can exhaust the cap before completion.',
                       'Current code rates and a fixed 200 JPY/USD are assumptions, not a new pricing or FX lookup.',
                       'Research before the saved checkpoint and historical test spending are not included in this new-run estimate.']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--saved-run',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':'focused'}), \
         patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')), \
         patch.object(socket,'create_connection',side_effect=AssertionError('offline only')):
        result=estimate(args.saved_run)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
