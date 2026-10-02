"""Durable per-job budget for opt-in tiered review on a single executor.

The experimental mode requires an explicit persistent directory. Not a
cross-host spending lock; enable on one executor per job until deployment.
"""
import contextvars,fcntl,hashlib,json,os,time
from contextlib import contextmanager
from pathlib import Path
from functools import wraps
JOB=contextvars.ContextVar('quality_budget_job',default=None)
STAGE=contextvars.ContextVar('quality_budget_stage',default='quality')
GENERATION_STAGES={'serp','search_intent','research_plan','fact_sheet','reference_structure','content_contract','outline','service_map','article','cta_inject'}
CLAUDE_RATES={'claude-opus-5-5':(8.,20.),'claude-opus-4-8':(10.,25.),'claude-sonnet-4-6':(6.,15.)}
RATES={'gpt-6-luna':(.125,.5),'gpt-6.1-sol':(2.5,10.)} # cache-write input ceiling
LIMIT=1.25 # <=250 JPY at conservative 200 JPY/USD allowance, 50 JPY headroom

@contextmanager
def scope(job_id,stage=None):
 token=JOB.set(str(job_id))
 stage_token=STAGE.set(stage or STAGE.get())
 try:yield
 finally:
  STAGE.reset(stage_token);JOB.reset(token)

def scoped(fn,stage=None):
 @wraps(fn)
 def wrapped(job_id,*args,**kwargs):
  with scope(job_id,stage):return fn(job_id,*args,**kwargs)
 return wrapped

@contextmanager
def ledger():
 from .content_quality import ContentQualityError
 job=JOB.get();folder=os.getenv('QUALITY_BUDGET_DIR')
 if not job or not folder:raise ContentQualityError('段階式確認にはジョブ別の予算管理設定が必要です。')
 root=Path(folder);root.mkdir(parents=True,exist_ok=True)
 key=hashlib.sha256(job.encode()).hexdigest();path=root/(key+'.json')
 with (root/(key+'.lock')).open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  value=json.loads(path.read_text()) if path.exists() else {'job_id':job,'limit_usd':LIMIT,'calls':[]}
  if value['job_id']!=job or value['limit_usd']!=LIMIT:raise ContentQualityError('保存済み予算設定が一致しません。')
  try:yield value
  finally:
   temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)

def completion_evaluation(value):
 """Explicit local evaluation: monitor observed total instead of worst-case holds.

 This is a spending checkpoint, not a guaranteed bill cap for an in-flight call.
 The default production-style reservation guard stays unchanged.
 """
 if os.getenv('QUALITY_COMPLETION_EVAL')!='1':return False
 from .content_quality import ContentQualityError
 policy=value.get('completion_evaluation') or {}
 if policy.get('stop_after_usd')!=10.0 or not policy.get('authorization'):
  raise ContentQualityError('完了検証用の費用設定・承認記録がありません。')
 used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
 if used>=policy['stop_after_usd']:
  raise ContentQualityError('完了検証の累積費用が見直し額に達しました。次の要求は送信しません。')
 return True


def reserve(payload):
 from .content_quality import ContentQualityError
 model=payload['model']
 if model not in RATES:raise ContentQualityError('予算対象外のモデルへ自動変更しません。')
 size=len(json.dumps(payload,ensure_ascii=False).encode())+4096
 if size>1000000:raise ContentQualityError('確認資料が大きすぎます。資料範囲を見直してください。')
 amount=(size*RATES[model][0]*(2 if size>272000 else 1)+payload['max_output_tokens']*RATES[model][1]*(1.5 if size>272000 else 1))/1e6
 with ledger() as value:
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
  if not completion_evaluation(value) and used+amount>LIMIT:raise ContentQualityError('品質確認の予算上限に達するため停止しました。未確認を合格扱いしません。')
  index=len(value['calls']);value['calls'].append({'model':model,'reserved_usd':amount,'status':'pending','started_at':time.time(),'provider':'openai','category':'quality','stage':STAGE.get()})
 return index

def settle(index,usage=None):
 with ledger() as value:
  call=value['calls'][index]
  if usage:
   if call.get('provider')=='anthropic':
    ir,orr=CLAUDE_RATES[call['model']]
    inputs=sum(usage.get(k,0) or 0 for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens'))
    cost=(inputs*ir+usage['output_tokens']*orr)/1e6 + (usage.get('server_tool_use') or {}).get('web_search_requests',0)*.01
   else:
    ir,orr=RATES[call['model']]
    long_context=usage['input_tokens']>272000
    cost=(usage['input_tokens']*ir*(2 if long_context else 1)+usage['output_tokens']*orr*(1.5 if long_context else 1))/1e6
   call.update(usage=usage,cost_usd=cost,status='accounted')
  else:call['status']='unknown_cost_reserved'


def reserve_claude(payload):
 """Record generation separately; charge corrective research to quality too."""
 from .content_quality import ContentQualityError
 model=payload['model']
 if model not in CLAUDE_RATES:raise ContentQualityError('費用未定義のモデルは実行しません。')
 category='generation' if STAGE.get() in GENERATION_STAGES else 'quality'
 size=len(json.dumps(payload,ensure_ascii=False).encode())+4096
 searches=0
 for tool in payload.get('tools',[]):
  if tool.get('type','').startswith('web_search'):
   searches+=tool.get('max_uses',10)
 # Server-side search may expand the input up to the model context. Do not
 # promise a hard cap based only on the small outbound request in that case.
 if searches:size=max(size,(searches+1)*1000000)
 ir,orr=CLAUDE_RATES[model]
 amount=(size*ir+payload['max_tokens']*orr)/1e6+searches*.01
 with ledger() as value:
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
  evaluation=completion_evaluation(value)
  if not evaluation and category=='quality' and used+amount>LIMIT:
   raise ContentQualityError('追加調査・修正を含む品質確認の予算上限を超えるため送信前に停止しました。')
  index=len(value['calls'])
  value['calls'].append({'model':model,'provider':'anthropic','category':category,'stage':STAGE.get(),
    'reserved_usd':amount,'status':'pending','started_at':time.time()})
 return index
