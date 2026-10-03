"""No model calls: size actual request builders using saved, explicitly mixed fixtures.

The old article is a size fixture, NOT a newly generated article or a quality pass.
Local o200k counts are estimates, not provider billing counts.
"""
import copy,json,os,socket,sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import tiktoken
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from pipeline import content_quality as cq,focused_quality as fq,tiered_research as tr
from pipeline.quality_budget import RATES


def run(research,article,capture=None):
 enc=tiktoken.get_encoding('o200k_base')
 load=lambda d:{p.stem:json.loads(p.read_text()) for p in d.glob('*.json')}
 r,a=load(research),load(article)
 get=lambda values,key:values[key]['content_text']
 plan=json.loads(get(r,'research_plan'));pages=json.loads(get(r,'fresh_sources'))
 job=json.loads((research.parent/'job.json').read_text())
 req=cq.requirements_for({k:v for k,v in job.items() if k!='id'},job['main_keyword'])
 from pipeline.step_review import SYSTEM_PROMPT
 req.update(research_plan=plan,research_decisions=json.loads(get(r,'research_matrix_1')),
            editorial_rules=SYSTEM_PROMPT.split('【チェック・修正項目】',1)[1].split('【出力フォーマット】',1)[0])
 text=get(a,'article');outline=get(a,'outline');facts=get(a,'fact_sheet')
 contract=json.loads(get(a,'content_contract'));sources=cq.source_evidence(r['fresh_sources'])
 rows=[];ratios=[]
 def size(request):
  return len(enc.encode(json.dumps(request,ensure_ascii=False),disallowed_special=()))
 def row(stage,request,before=None,actual=None):
  if capture:capture(stage,copy.deepcopy(request))
  n=size(request);old=size(before or request)
  rows.append({'stage':stage,'model':request['model'],'before_local_input_tokens':old,
               'after_local_input_tokens':n,'output_token_cap':request['max_tokens'],
               'observed_input_tokens_if_identical':actual})
  if actual:ratios.append(actual/n)
 def research_send(job,step,request):
  payload=json.loads(request['messages'][0]['content']);saved=r.get(step)
  exact=saved and saved.get('meta',{}).get('request_sha256')==cq.digest(json.dumps(request,ensure_ascii=False,sort_keys=True))
  row(step,request,actual=saved['meta'].get('input_tokens') if exact else None)
  if 'decisions' in payload:return {'coverage_sufficient':False,'coverage_reason':'OFFLINE SIZE ONLY','coverage_issues':[]},NS(input_tokens=0,output_tokens=0)
  ids={q['id'] for q in payload['plan']['items']};result=json.loads(saved['content_text'])
  result['items']=[i for i in result['items'] if i['id'] in ids]
  assert {i['id'] for i in result['items']}==ids
  return result,NS(input_tokens=0,output_tokens=0)
 with patch.object(tr,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(r.get(s))),patch.object(tr,'cached_result',return_value=None),patch.object(tr,'checked_request',side_effect=research_send):
  tr.audit_matrix('offline',plan,pages,get(r,'fact_sheet'),{k:get(r,k) for k in ('serp','search_intent')})
 class Captured(Exception):pass
 def capture_outline(*args,**request):
  previous=copy.deepcopy(request);payload=json.loads(previous['messages'][0]['content'])
  payload.update(requirements=req,outline=outline);payload.pop('outline_is_document',None)
  previous['messages'][0]['content']=json.dumps(payload,ensure_ascii=False)
  row('outline_audit',request,previous);raise Captured()
 with patch.object(cq,'create_with_retry',side_effect=capture_outline):
  try:cq.audit(None,stage='research',text=outline,facts=facts,outline=outline,contract=contract,requirements=req,sources=sources)
  except Captured:pass
 def final_send(job,step,request):
  previous=copy.deepcopy(request);payload=json.loads(previous['messages'][0]['content']);payload['requirements']=req
  previous['messages'][0]['content']=json.dumps(payload,ensure_ascii=False)
  row(step,request,previous)
  keys=fq.ROLES[step.removeprefix('tiered_article_')]
  return {'checks':[{'key':k,'status':'pass','reason':'SIZE FIXTURE ONLY','affected_blocks':[]} for k in keys]},NS(input_tokens=0,output_tokens=0)
 with patch.object(tr,'checked_request',side_effect=final_send):
  fq.audit_article(None,text=text,facts=facts,outline=outline,contract=contract,requirements=req,sources=sources)
 # Repair is a size scenario, not an actual failed audit or generated patch.
 repair={'model':'gpt-6.1-sol','max_tokens':10000,'system':'Repair prompt size allowance '+('x '*2000),
         'messages':[{'role':'user','content':json.dumps({'confirmed_facts':facts,'source_documents':sources,
            'requirements':req,'content_contract':contract,'article_blocks':fq.content_blocks(text),
            'conditional_facts':cq.conditional_facts(facts),'failed_checks':[]},ensure_ascii=False)}]}
 row('article_repair_scenario',repair)
 factor=max([1.2]+[v*1.05 for v in ratios])
 for v in rows:
  ir,out=RATES[v['model']]
  for side in ('before','after'):
   count=v[side+'_local_input_tokens']*factor
   v[side+'_usd_with_output_cap']=(count*ir*(2 if count>272000 else 1)+v['output_token_cap']*out*(1.5 if count>272000 else 1))/1e6
 normal=[v for v in rows if v['stage']!='article_repair_scenario']
 final=[v for v in rows if v['stage'].startswith('tiered_article_')]
 return {'api_calls':0,'tokenizer':'o200k_base (approximation for requested models)',
         'calibration_ratio_range':[min(ratios),max(ratios)] if ratios else None,'safety_multiplier':factor,
         'article_chars':len(text),'matrix_fixture_chars':len(json.dumps(req['research_decisions'],ensure_ascii=False)),
         'mixed_fixture_warning':'Current research records + previously completed article for volume only. No new article, no quality verdict.',
         'preset_settings_included':True,'learned_style_rules_unavailable_in_local_fixture':True,
         'generation_cost_excluded':True,'supplemental_research_cost_excluded_from_scenarios':True,
         'normal_before_usd':sum(v['before_usd_with_output_cap'] for v in normal),
         'normal_after_usd':sum(v['after_usd_with_output_cap'] for v in normal),
         'after_one_article_repair_and_full_reaudit_usd':sum(v['after_usd_with_output_cap'] for v in rows)+sum(v['after_usd_with_output_cap'] for v in final),
         'rows':rows}

if __name__=='__main__':
 with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered'}),patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),patch.object(socket,'create_connection',side_effect=AssertionError('No network')):
  report=run(Path(sys.argv[1]),Path(sys.argv[2]))
 Path(sys.argv[3]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
