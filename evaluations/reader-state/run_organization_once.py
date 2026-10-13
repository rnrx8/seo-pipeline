"""Use the existing Responses adapter, reserving against the experiment ledger."""
import fcntl,json,sys,time,os
from pathlib import Path
from unittest.mock import patch
from pipeline.openai_review import create_review_response
r=Path(sys.argv[1]);arm=sys.argv[2];assert arm=='organization_review'
assert bool(os.getenv('OPENAI_API_KEY','').strip()),'Existing credential missing'
with (r/'budget.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX);b=json.loads((r/'budget.json').read_text());assert b['approved'] and b['max_total_jpy']==200 and arm not in b['calls']
 req=json.loads((r/f'{arm}-request.json').read_text());assert req['model']=='gpt-6.1-sol' and req['max_tokens']==6000
 def save():
  temp=r/'budget.tmp';temp.write_text(json.dumps(b,indent=2));temp.replace(r/'budget.json')
 def reserve(payload,input_counter):
  count=input_counter(payload);bound=(count*2.5+payload['max_output_tokens']*10)/1e6*200
  assert bound<=22,f'Counted ceiling {bound:.2f} JPY exceeds 22 JPY allocation; not sent'
  used=sum(v.get('cost_jpy',v['reserved_jpy']) for v in b['calls'].values());assert used+22+13<=200,'Cannot reserve review plus optional edit'
  b['calls'][arm]={'status':'pending','reserved_jpy':22,'input_bound':count,'counted_ceiling_jpy':bound};save();return arm
 def settle(_,usage=None):
  rec=b['calls'][arm]
  if usage:rec.update(status='accounted',usage=usage,cost_jpy=(usage['input_tokens']*2.5+usage['output_tokens']*10)/1e6*200)
  else:rec['status']='unknown_cost_reserved'
  save()
 with patch('pipeline.quality_budget.reserve',side_effect=reserve),patch('pipeline.quality_budget.settle',side_effect=settle):
  result=create_review_response(**req)
 out='\n'.join(x.text for x in result.content);json.loads(out);(r/f'{arm}.md').write_text(out);print(json.dumps(b['calls'][arm]))
