"""Offline scope/size diagnostics, never a quality or cost-completion verdict."""
import ast,json,os,socket,sys,subprocess
from pathlib import Path
from unittest.mock import patch
from collections import Counter
import tiktoken
from audit_full_flow_cost import run
from focused_packets import build,audit_payload,fingerprint
from pipeline import content_quality as cq,tiered_research as tr

def saved_excerpt_policy():
    source=subprocess.check_output(['git','show','a32eab7:pipeline/content_quality.py'],cwd=Path(__file__).resolve().parents[2],text=True)
    tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='source_evidence')
    namespace=dict(vars(cq));exec(compile(ast.Module(body=[node],type_ignores=[]),'<saved-excerpt-policy>','exec'),namespace)
    return namespace['source_evidence']


def measure(research,article):
    captured=[]
    all_requests={}
    def capture(stage,request):
        all_requests[stage]=request
        if stage.startswith('tiered_adjudication_'):captured.append({'stage':stage,'request':request})
    # Saved model replies bind the original excerpts. Do not replay their local
    # span IDs against the newly fixed excerpt allocation and call that valid.
    prior=saved_excerpt_policy()
    with patch.object(cq,'source_evidence',prior),patch.object(tr,'source_evidence',prior):
        run(research,article,capture=capture)
    bundle=build(captured)
    encode=tiktoken.get_encoding('o200k_base').encode
    count=lambda v:len(encode(json.dumps(v,ensure_ascii=False,separators=(',',':')),disallowed_special=()))
    payload=audit_payload(bundle)
    # Include the original factual audit instructions and output schema once.
    packet={'system':captured[0]['request']['system'],'output_config':captured[0]['request']['output_config'],'payload':payload}
    all_ids=[i['question']['id'] for i in bundle['audits']+bundle['lookup']]
    expected=[q['id'] for x in captured for q in json.loads(x['request']['messages'][0]['content'])['plan']['items']]
    assert set(all_ids)==set(expected) and len(all_ids)==len(expected)
    for item in bundle['audits']+bundle['lookup']:
        assert not item['approved']
        for key in item['sources']:assert fingerprint(bundle['source_store'][key])==key
    tokens=count(packet)
    routes=Counter(i['route'] for i in bundle['audits']+bundle['lookup'])
    records={p.stem:json.loads(p.read_text()) for p in research.glob('*.json')}
    full={p['url']:p for p in json.loads(records['fresh_sources']['content_text']) if p.get('status')=='success' and p.get('text')}
    grouped={}
    for item in bundle['lookup']:grouped.setdefault(item['origin'],[]).append(item)
    lookups=[]
    for origin,items in grouped.items():
        urls={u for i in items for u in i['candidate'].get('official_checked_urls',[]) if u in full}
        # If no recorded official body exists, measure the entire saved source
        # set available to this group. This is a conservative input scenario,
        # NOT a retrieval or a verdict, and is not silently clipped to fit.
        if not urls:urls={bundle['source_store'][k]['url'] for i in items for k in i['sources'] if bundle['source_store'][k]['url'] in full}
        data={'questions':[i['question'] for i in items],'unresolved_reasons':[i['candidate'].get('reason','') for i in items],
              'sources':[full[u] for u in sorted(urls)]}
        n=count({'system':captured[0]['request']['system'],'output_config':captured[0]['request']['output_config'],'payload':data})
        bound=int(n*1.2+.9999);usd=(bound*.125*(2 if bound>272000 else 1)+6000*.5*(1.5 if bound>272000 else 1))/1e6
        lookups.append({'origin':origin,'questions':[i['question']['id'] for i in items],'sources':len(urls),'input_local_tokens':n,'input_with_margin':bound,'output_cap':6000,'luna_scenario_usd':usd})
    price_url='https://www.cuddle-jp.com/pricing.html'
    origin=next(x for x in captured if any(q['id']=='q34' for q in json.loads(x['request']['messages'][0]['content'])['plan']['items']))
    original_sources=json.loads(origin['request']['messages'][0]['content'])['sources']
    oldprice=next(p for p in original_sources if p['url']==price_url)
    selected=[full[p['url']] for p in original_sources if p['url'] in full]
    # Same stored documents and budget. Show redistribution visibility separately
    # from the new focused lookup route, which can read the whole 5,576-char page.
    artifact={'content_text':json.dumps(selected,ensure_ascii=False)}
    oldview=next(p for p in json.loads(prior(artifact,max_chars=60000)) if p['url']==price_url)
    newview=next(p for p in json.loads(cq.source_evidence(artifact,max_chars=60000)) if p['url']==price_url)
    screen=all_requests['tiered_screen_3']
    matched=records['tiered_screen_3']['meta']['request_sha256']==cq.digest(json.dumps(screen,ensure_ascii=False,sort_keys=True))
    assert matched, 'Do not attribute a model miss to a reconstructed different request'
    assert json.loads(screen['messages'][0]['content'])['sources']==original_sources
    price_proof={'recorded_screen_request_exactly_matched':matched,'stored_body_chars':len(full[price_url]['text']),
                 'original_model_view_contains_price_and_tax':'1 ヶ月 プラン （30日） ¥ 9,980 ・税込表示です。' in ''.join(e['text'] for e in oldprice['excerpts']),
                 'equal_allocation_contains_price_and_tax':'1 ヶ月 プラン （30日） ¥ 9,980 ・税込表示です。' in oldview['text'],
                 'redistributed_allocation_contains_price_and_tax':'1 ヶ月 プラン （30日） ¥ 9,980 ・税込表示です。' in newview['text'],
                 'full_saved_body_contains_month_and_tax':'1 ヶ月 プラン （30日） ¥ 9,980 ・税込表示です。' in full[price_url]['text'],
                 'automatic_fact_verdict_produced':False}
    return {'api_calls':0,'runtime_enabled':False,'quality_validated':False,'all_questions_preserved':True,
            'original_sol_questions':len(expected),'routing':dict(routes),
            'initial_sol_input_local_tokens':tokens,'initial_sol_input_with_20_percent_margin':int(tokens*1.2+.9999),
            'fits_60000_initial_input_with_margin':tokens*1.2<=60000,
            'initial_sol_cost_scenario_usd':(int(tokens*1.2+.9999)*2.5+10000*10)/1e6,
            'initial_sol_output_cap_scenario':10000,'pricing_basis':'Existing conservative repository constants, not current invoice or provider quote',
            'source_records_in_store':len(bundle['source_store']),'source_records_in_initial_audit':len(bundle['initial_audit_sources']),
            'all_previously_visible_source_records_retained':True,
            'no_full_source_claim':'Sources preserve prior excerpt/truncation boundaries. Not necessarily complete fetched web pages.',
            'expansion_requests_unmeasured':True,'lookup_cost_unmeasured':True,'model_quality_unmeasured':True,
            'entire_300_yen_budget_proven':False,
            'saved_lookup_scenarios':lookups,'saved_lookup_scenario_usd':sum(x['luna_scenario_usd'] for x in lookups),
            'lookup_scenario_excludes_new_search_or_sol_fallback':True,'missing_price_excerpt_proof':price_proof,
            'queues':[{'id':i['question']['id'],'subject':i['question']['subject'],'route':i['route'],
                       'required':i['question']['required'],'approved':i['approved']} for i in bundle['audits']+bundle['lookup']]}

if __name__=='__main__':
    with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}),patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),patch.object(socket,'create_connection',side_effect=AssertionError('No network')):
        result=measure(Path(sys.argv[1]),Path(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='queues'},ensure_ascii=False,indent=2))
