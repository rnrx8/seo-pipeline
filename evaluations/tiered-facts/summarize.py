"""Score frozen cases without any API calls. No model is used as the grader."""
import json
from pathlib import Path
from collections import Counter
P=Path(__file__).parent
cases={c['id']:c for f in ('cases.json','holdout.json') for c in json.loads((P/f).read_text())['cases']}
results={};stats={}
for model in ('gpt-6-luna','gpt-6.1-sol'):
 rows=[x for f in (P/'results').glob('*.json') if model in f.name and 'response' not in f.name for x in json.loads(f.read_text())['result']['checks']]
 values={r['id']:r for r in rows};assert len(rows)==len(values)==len(cases)
 results[model]=values
 stats[model]={'cases':len(values),'exact':sum(v['verdict']==cases[k]['expected'] for k,v in values.items()),'false_accept':sum(v['verdict']=='supported' and cases[k]['expected']!='supported' for k,v in values.items()),'false_reject':sum(v['verdict']!='supported' and cases[k]['expected']=='supported' for k,v in values.items()),'mismatches':[{'id':k,'expected':cases[k]['expected'],**v} for k,v in values.items() if v['verdict']!=cases[k]['expected']]}
escalated=[k for k,v in results['gpt-6-luna'].items() if v['verdict']!='supported']
routed={k:results['gpt-6.1-sol'][k] if k in escalated else v for k,v in results['gpt-6-luna'].items()}
ledger=json.loads((P/'results/ledger.json').read_text())
summary={'scope':'Small fixed-evidence pilot, not normal article flow or source retrieval validation. Follow-up uses same service domain.','labels':dict(Counter(c['expected'] for c in cases.values())),'models':stats,'routed_replay':{'description':'Offline replay of actual independent model outputs; all non-supported Luna cases use Sol. No gold labels in dispatch. Not a production routing implementation.','escalated':len(escalated),'exact':sum(v['verdict']==cases[k]['expected'] for k,v in routed.items())},'accounting':{'method':ledger['cost_method'],'ceiling_usd':sum(c.get('cost_usd',c['reserved_usd']) for c in ledger['calls']),'yen_allowance_per_usd':200,'call_statuses':dict(Counter(c['status'] for c in ledger['calls']))},'limitations':['One failed Luna request lacked raw-response persistence; exact validation failure unknown. One controlled retry passed; both charged usages counted. Subsequent raw responses retained locally.','Curated passages and seeded errors, not representative error-frequency sampling.','Luna-supported false negatives cannot be rescued by uncertainty-only routing; larger blind tests and pass sampling still needed.','No full article generation, prose/coherence/rendering evaluation, or deployment.']}
(P/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary['accounting'],ensure_ascii=False))
