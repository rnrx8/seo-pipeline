"""Durable per-job budget for opt-in tiered review on a single executor.

The experimental mode requires an explicit persistent directory. Not a
cross-host spending lock; enable on one executor per job until deployment.
"""
import contextvars,fcntl,hashlib,json,os,time,math
from contextlib import contextmanager
from pathlib import Path
from functools import wraps
JOB=contextvars.ContextVar('quality_budget_job',default=None)
STAGE=contextvars.ContextVar('quality_budget_stage',default='quality')
GENERATION_STAGES={'serp','search_intent','research_plan','fact_sheet','reference_structure','content_contract','outline','service_map','article','cta_inject'}
CLAUDE_RATES={'claude-opus-5-5':(8.,20.),'claude-opus-4-8':(10.,25.),'claude-sonnet-4-6':(6.,15.)}
# Standard API prices checked 2026-10-03 at platform.claude.com/docs/en/about-claude/pricing.
CLAUDE_USAGE_RATES={'claude-opus-5-5':(4.,5.,8.,.2,20.),'claude-opus-4-8':(5.,6.25,10.,.5,25.),'claude-sonnet-4-6':(3.,3.75,6.,.3,15.)}
RATES={'gpt-6-luna':(.125,.5),'gpt-6.1-sol':(2.5,10.)} # cache-write input ceiling
LIMIT=1.25 # <=250 JPY at conservative 200 JPY/USD allowance, 50 JPY headroom

REQUEST=contextvars.ContextVar('quality_review_request',default=None)
# These ceilings divide the existing quality allowance; they do not raise it.
REVIEW_PHASE_LIMITS={'outline':.5,'article':LIMIT}


def review_operation(step):
 if step=='tiered_audit_research':return 'outline','audit',2
 if step=='outline_local_repair_response':return 'outline','repair',1
 if step.startswith('tiered_research_evidence_'):return 'outline',step.removeprefix('tiered_research_evidence_'),2
 if step.startswith('tiered_article_'):return 'article',step.removeprefix('tiered_article_'),2
 if step.startswith('tiered_content_repair_'):return 'article','repair',2
 return None


@contextmanager
def request_scope(job_id,step,fingerprint,request):
 """Bind direct helper calls to the same durable limits as normal execution."""
 operation=review_operation(step)
 if operation is None:
  yield
  return
 try:data=json.loads(request['messages'][0]['content'])
 except (ValueError,KeyError,TypeError):data={}
 phase,role,limit=operation
 meta={'phase':phase,'role':role,'call_limit':limit,'step':step,'request_sha256':fingerprint,
       'target_block_ids':data.get('target_block_ids',[]),
       'shown_block_ids':[b['id'] for b in data.get('article_blocks',data.get('outline_blocks',[]))],
       'research_ids':sorted({q for c in data.get('candidate_checks',data.get('failed_checks',[])) for q in c.get('research_ids',[])}),
       'source_urls':[p['url'] for p in data.get('source_documents',[]) if isinstance(p,dict) and 'url' in p],
       'reasons':[c.get('reason','') for c in data.get('candidate_checks',data.get('failed_checks',[]))]}
 token=REQUEST.set(meta)
 try:
  with scope(job_id,'research_validation' if phase=='outline' else 'content_audit'):yield
 finally:REQUEST.reset(token)


def guard_review(value,amount):
 """A restarted process never resets the per-stage limits or pending requests."""
 from .content_quality import ContentQualityError
 meta=REQUEST.get()
 if not meta:
  if STAGE.get() in ('research_validation','content_audit','outline_local_repair'):
   raise ContentQualityError('確認処理の種別・対象が記録されていません。送信しません。')
  return
 calls=value['calls']
 # An old unscoped review cannot silently receive a fresh allowance on upgrade.
 legacy=[c for c in calls if not c.get('review_operation') and c.get('stage') in
         (('research_validation','outline_local_repair') if meta['phase']=='outline' else ('content_audit',))]
 if legacy:raise ContentQualityError('旧方式の確認履歴があります。通算予算・回数の移行確認前には再開しません。')
 policy={'version':'bounded-review-v2-shared-remainder','phase_limits_usd':REVIEW_PHASE_LIMITS,
         'outline_audits':2,'outline_repairs':1,'article_role_checks':2,'article_repairs_including_encoding':2}
 saved=value.setdefault('review_policy',policy)
 if saved!=policy:
  raise ContentQualityError('保存済みの確認回数・費用配分が現在の設定と異なります。自動変更しません。')
 previous=[c for c in calls if c.get('review_operation',{}).get('phase')==meta['phase']]
 same=[c for c in previous if c['review_operation']['role']==meta['role']]
 if meta['role'] in ('screen','evidence_screen') and any(c['review_operation']['role']=='repair' for c in previous):
  allowed_urls={u for c in same for u in c['review_operation'].get('source_urls',[])}
  allowed_questions={q for c in same for q in c['review_operation'].get('research_ids',[])}
  if not same or not set(meta['source_urls'])<=allowed_urls or not set(meta['research_ids'])<=allowed_questions:
   raise ContentQualityError('修正後に新しい照合論点・資料が追加されています。範囲を確認するまで送信しません。')
 if any(c['review_operation']['request_sha256']==meta['request_sha256'] for c in same):
  raise ContentQualityError('同じ確認要求の送信記録があります。保存応答または未確定費用を確認するまで再送しません。')
 if len(same)>=meta['call_limit']:
  raise ContentQualityError('再開を含む確認・修正の通算回数上限です。原因と範囲の確認が必要です。')
 used=sum(c.get('cost_usd',c['reserved_usd']) for c in previous)
 quality_used=sum(c.get('cost_usd',c['reserved_usd']) for c in calls if c.get('category','quality')=='quality')
 if used+amount>REVIEW_PHASE_LIMITS[meta['phase']] or quality_used+amount>LIMIT:
  raise ContentQualityError('この確認段階の費用枠を超えます。後工程の予算を消費せず送信前に停止しました。')


def review_remaining(value):
 meta=REQUEST.get()
 if not meta:return float('inf')
 used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls']
          if c.get('review_operation',{}).get('phase')==meta['phase'])
 quality_used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
 return min(REVIEW_PHASE_LIMITS[meta['phase']]-used,LIMIT-quality_used)


def category_remaining(value, category):
 """Optional trial allocation: generation cannot consume review reserves."""
 from .content_quality import ContentQualityError
 caps=value.get('category_limits_usd')
 if caps is None:return float('inf')
 if (not isinstance(caps,dict) or set(caps)!={'generation','quality'}
     or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in caps.values())):
  raise ContentQualityError('生成・品質確認の費用配分が不正です。')
 used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')==category)
 return caps[category]-used


def guard_category(value, category, amount):
 from .content_quality import ContentQualityError
 if amount>category_remaining(value,category):
  raise ContentQualityError('生成・品質確認それぞれの費用枠を超えるため、他工程の予算を使わず停止しました。')


def review_metadata():
 return {'review_operation':REQUEST.get()} if REQUEST.get() else {}

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
 threshold=policy.get('stop_after_usd')
 if (not isinstance(threshold,(int,float)) or isinstance(threshold,bool)
     or not math.isfinite(threshold) or threshold<=0 or not policy.get('authorization')
     or (threshold!=10.0 and threshold!=value.get('total_limit_usd'))):
  raise ContentQualityError('完了検証用の費用設定・承認記録がありません。')
 used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
 if used>=policy['stop_after_usd']:
  raise ContentQualityError('完了検証の累積費用が見直し額に達しました。次の要求は送信しません。')
 return True


def guard_total(value, amount):
 """Optional stricter total cap for a bounded evaluation, never a budget reset."""
 from .content_quality import ContentQualityError
 configured=os.getenv('QUALITY_TOTAL_LIMIT_USD')
 saved=value.get('total_limit_usd')
 if configured is not None:
  try:limit=float(configured)
  except ValueError:raise ContentQualityError('全体予算上限が不正です。') from None
  if not math.isfinite(limit) or limit<=0:raise ContentQualityError('全体予算上限が不正です。')
  if saved is not None and saved!=limit:raise ContentQualityError('保存済み全体予算上限を変更できません。')
  value['total_limit_usd']=limit;saved=limit
 if saved is not None:
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
  if used+amount>saved:raise ContentQualityError('生成を含むテスト全体の予算上限に達するため、送信前に停止しました。')


def reserve(payload, input_counter=None):
 from .content_quality import ContentQualityError
 model=payload['model']
 if model not in RATES:raise ContentQualityError('予算対象外のモデルへ自動変更しません。')
 size=len(json.dumps(payload,ensure_ascii=False,default=lambda v:v.model_dump(mode='json')).encode())+4096
 if size>1000000:raise ContentQualityError('確認資料が大きすぎます。資料範囲を見直してください。')
 amount=(size*RATES[model][0]*(2 if size>272000 else 1)+payload['max_output_tokens']*RATES[model][1]*(1.5 if size>272000 else 1))/1e6
 with ledger() as value:
  guard_review(value,0)
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
  evaluation=completion_evaluation(value)
  # Bytes are a safe initial bound, but Japanese JSON can greatly overstate
  # token usage. Refine only rejected requests; never relax either budget.
  guard_total(value,0)
  total_used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
  remaining=min(category_remaining(value,'quality'),review_remaining(value),float('inf') if evaluation else LIMIT-used,
                value.get('total_limit_usd',float('inf'))-total_used)
  counted=None
  output_floor=payload['max_output_tokens']*RATES[model][1]/1e6
  if amount>remaining and input_counter is not None and output_floor<remaining:
   fingerprint=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
   counts=value.setdefault('input_counts',{})
   record=counts.get(fingerprint)
   if record is None:
    counts[fingerprint]={'status':'attempted'}
    counted=input_counter(payload)
    if type(counted) is not int or counted<=0:raise ContentQualityError('入力トークン数を安全に確認できませんでした。')
    record=counts[fingerprint]={'status':'counted','input_token_bound':counted}
   if record['status']!='counted':raise ContentQualityError('入力トークン数の確認が未完了です。自動再試行しません。')
   counted=record['input_token_bound']
   amount=(counted*RATES[model][0]*(2 if counted>272000 else 1)+payload['max_output_tokens']*RATES[model][1]*(1.5 if counted>272000 else 1))/1e6
  guard_total(value,amount)
  guard_review(value,amount)
  guard_category(value,'quality',amount)
  if not evaluation and used+amount>LIMIT:raise ContentQualityError('品質確認の予算上限に達するため停止しました。未確認を合格扱いしません。')
  index=len(value['calls']);value['calls'].append({'model':model,'reserved_usd':amount,'status':'pending','started_at':time.time(),'provider':'openai','category':'quality','stage':STAGE.get(),**review_metadata(),**({'input_token_bound':counted} if counted is not None else {})})
 return index

def settle(index,usage=None):
 with ledger() as value:
  call=value['calls'][index]
  if usage:
   if call.get('provider')=='anthropic':
    ir,w5,w60,read,orr=CLAUDE_USAGE_RATES[call['model']]
    cache=usage.get('cache_creation') or {}
    five=cache.get('ephemeral_5m_input_tokens',0) or 0
    hour=cache.get('ephemeral_1h_input_tokens',0) or 0
    unknown=max(0,(usage.get('cache_creation_input_tokens',0) or 0)-five-hour)
    cost=((usage.get('input_tokens',0) or 0)*ir+five*w5+(hour+unknown)*w60
          +(usage.get('cache_read_input_tokens',0) or 0)*read+usage['output_tokens']*orr)/1e6
    cost+=(usage.get('server_tool_use') or {}).get('web_search_requests',0)*.01
    call.update(cost_basis='usage_standard_rates_20261003',unknown_cache_write_tokens=unknown)
   else:
    ir,orr=RATES[call['model']]
    long_context=usage['input_tokens']>272000
    cost=(usage['input_tokens']*ir*(2 if long_context else 1)+usage['output_tokens']*orr*(1.5 if long_context else 1))/1e6
   call.update(usage=usage,cost_usd=cost,status='accounted')
  else:call['status']='unknown_cost_reserved'


def reserve_claude(payload, input_counter=None):
 """Record generation separately; charge corrective research to quality too."""
 from .content_quality import ContentQualityError
 model=payload['model']
 if model not in CLAUDE_RATES:raise ContentQualityError('費用未定義のモデルは実行しません。')
 category='generation' if STAGE.get() in GENERATION_STAGES else 'quality'
 size=len(json.dumps(payload,ensure_ascii=False,default=lambda v:v.model_dump(mode='json')).encode())+4096
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
  guard_review(value,0)
  guard_total(value,0)
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
  evaluation=completion_evaluation(value)
  total_used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'])
  remaining=min(category_remaining(value,category),review_remaining(value),value.get('total_limit_usd',float('inf'))-total_used,
                LIMIT-used if category=='quality' and not evaluation else float('inf'))
  if amount>remaining and input_counter is not None and not payload.get('tools'):
   key=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,default=lambda v:v.model_dump(mode='json')).encode()).hexdigest()
   counts=value.setdefault('input_counts',{});record=counts.get(key)
   if record is None:
    counts[key]={'status':'attempted'}
    counted=input_counter(payload)
    if type(counted) is not int or counted<=0:raise ContentQualityError('入力トークン数の確認に失敗しました。送信しません。')
    record=counts[key]={'status':'counted','input_token_bound':math.ceil(counted*1.1)+4096}
   if record['status']!='counted':raise ContentQualityError('入力トークン数の確認が未完了です。自動再試行しません。')
   amount=(record['input_token_bound']*ir+payload['max_tokens']*orr)/1e6
  guard_total(value,amount)
  guard_review(value,amount)
  guard_category(value,category,amount)
  if not evaluation and category=='quality' and used+amount>LIMIT:
   raise ContentQualityError('追加調査・修正を含む品質確認の予算上限を超えるため送信前に停止しました。')
  index=len(value['calls'])
  value['calls'].append({'model':model,'provider':'anthropic','category':category,'stage':STAGE.get(),
    'reserved_usd':amount,'status':'pending','started_at':time.time(),**review_metadata()})
 return index


def reserve_search(query):
 """Serper standard search: one credit; Starter published allowance $1/1000.

 Separate provider expenditure from inference. No free-credit assumption.
 """
 from .content_quality import ContentQualityError
 amount=.001
 with ledger() as value:
  guard_total(value,amount)
  used=sum(c.get('cost_usd',c['reserved_usd']) for c in value['calls'] if c.get('category','quality')=='quality')
  category='generation' if STAGE.get() in GENERATION_STAGES else 'quality'
  guard_category(value,category,amount)
  if not completion_evaluation(value) and category=='quality' and used+amount>LIMIT:
   raise ContentQualityError('追加検索の予算上限に達しました。')
  index=len(value['calls'])
  value['calls'].append({'provider':'serper','model':'search','category':category,'stage':STAGE.get(),
    'reserved_usd':amount,'status':'pending','started_at':time.time(),
    'query_sha256':hashlib.sha256(query.encode()).hexdigest()})
  return index


def settle_search(index,status):
 with ledger() as value:
  value['calls'][index].update(status='search_'+status,estimated_cost_usd=.001)
