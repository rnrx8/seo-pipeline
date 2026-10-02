"""Durable per-job budget for opt-in tiered review on a single executor.

The experimental mode requires an explicit persistent directory. Not a
cross-host spending lock; enable on one executor per job until deployment.
"""
import contextvars,fcntl,hashlib,json,os,time
from contextlib import contextmanager
from pathlib import Path
from functools import wraps
JOB=contextvars.ContextVar('quality_budget_job',default=None)
RATES={'gpt-6-luna':(.125,.5),'gpt-6.1-sol':(2.5,10.)} # cache-write input ceiling
LIMIT=1.25 # <=250 JPY at conservative 200 JPY/USD allowance, 50 JPY headroom

@contextmanager
def scope(job_id):
 token=JOB.set(str(job_id))
 try:yield
 finally:JOB.reset(token)

def scoped(fn):
 @wraps(fn)
 def wrapped(job_id,*args,**kwargs):
  with scope(job_id):return fn(job_id,*args,**kwargs)
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

def reserve(payload):
 from .content_quality import ContentQualityError
 model=payload['model']
 if model not in RATES:raise ContentQualityError('予算対象外のモデルへ自動変更しません。')
 size=len(json.dumps(payload,ensure_ascii=False).encode())+4096
 if size>1000000:raise ContentQualityError('確認資料が大きすぎます。資料範囲を見直してください。')
 amount=(size*RATES[model][0]*(2 if size>272000 else 1)+payload['max_output_tokens']*RATES[model][1]*(1.5 if size>272000 else 1))/1e6
 with ledger() as value:
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
  if used+amount>LIMIT:raise ContentQualityError('品質確認の予算上限に達するため停止しました。未確認を合格扱いしません。')
  index=len(value['calls']);value['calls'].append({'model':model,'reserved_usd':amount,'status':'pending','started_at':time.time()})
 return index

def settle(index,usage=None):
 with ledger() as value:
  call=value['calls'][index]
  if usage:
   ir,orr=RATES[call['model']]
   long_context=usage['input_tokens']>272000
   call.update(usage=usage,cost_usd=(usage['input_tokens']*ir*(2 if long_context else 1)+usage['output_tokens']*orr*(1.5 if long_context else 1))/1e6,status='accounted')
  else:call['status']='unknown_cost_reserved'
