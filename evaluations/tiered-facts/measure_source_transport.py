"""Offline comparison of equivalent evidence transport; not a quality verdict."""
import copy,json,socket,subprocess,sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from pipeline import tiered_research as tiered
from pipeline.source_spans import indexed_sources

def run(staged):
 records={p.stem:json.loads(p.read_text()) for p in staged.glob('*.json')}
 baseline={}
 exec(compile(subprocess.check_output(['git','show','dc00a57:pipeline/source_spans.py'],cwd=ROOT,text=True),'<baseline-spans>','exec'),baseline)
 old=baseline['indexed_sources'];source_sets={};rows=[]
 def remember(pages):
  visible,index=old(pages)
  source_sets[json.dumps(visible,ensure_ascii=False,sort_keys=True)]=copy.deepcopy(pages)
  return visible,index
 def response(job,step,request):
  payload=json.loads(request['messages'][0]['content']);updated=copy.deepcopy(request)
  if 'sources' in payload:
   pages=source_sets[json.dumps(payload['sources'],ensure_ascii=False,sort_keys=True)]
   new,_=indexed_sources(pages)
   # Prove the transformation retains every non-whitespace character, page and metadata.
   for page,visible in zip(pages,new):
    actual=''.join(x['text'] for x in visible['excerpts'])
    original=page.get('text','').replace('\n[中略：取得本文の抜粋]\n','')
    assert ''.join(actual.split())==''.join(original.split())
    assert {k:v for k,v in visible.items() if k!='excerpts'}=={k:v for k,v in page.items() if k!='text'}
   payload['sources']=new
   updated['messages']=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
  before=len(json.dumps(request,ensure_ascii=False).encode());after=len(json.dumps(updated,ensure_ascii=False).encode())
  rows.append({'step':step,'model':request['model'],'before_bytes':before,'after_bytes':after})
  data=json.loads(records[step]['content_text']) if step in records else {'items':[]}
  # Saved outputs are only used to exercise request construction, never approved.
  if 'items' in data:
   by_id={i['id']:i for i in data['items']}
   data['items']=[by_id.get(q['id'],{'id':q['id'],'status':'unresearched','answer':'','reason':'Offline response absent','evidence':[],'basis':'unresolved'}) for q in payload['plan']['items']]
  return data,NS(input_tokens=0,output_tokens=0)
 with patch.object(tiered,'get_optional_artifact',side_effect=lambda j,s:copy.deepcopy(records.get(s))),patch.object(tiered,'indexed_sources',side_effect=remember),patch.object(tiered,'checked_request',side_effect=response):
  tiered.audit_matrix('offline',json.loads(records['research_plan']['content_text']),json.loads(records['fresh_sources']['content_text']),records['fact_sheet']['content_text'],{})
 sol=[r for r in rows if r['model']=='gpt-6.1-sol']
 before=sum(r['before_bytes'] for r in sol);after=sum(r['after_bytes'] for r in sol)
 return {'external_api_calls':0,'source_text_and_metadata_preserved':True,'sol_before_bytes':before,'sol_after_bytes':after,'sol_input_byte_reduction':1-after/before,'quality_revalidated':False,'article_cost_verified':False,'requests':rows}
if __name__=='__main__':
 with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')):
  result=run(Path(sys.argv[1]))
 Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='requests'},indent=2))
