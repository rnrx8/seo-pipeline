"""Opt-in Luna screening, Sol adjudication, and compact coverage checking."""
import copy,json
from types import SimpleNamespace
from .ai import create_with_retry
from .db import get_optional_artifact,upsert_artifact
from .content_quality import response_text,digest,source_evidence
from .research_collection import batches
from .fresh_sources import extract_urls
from .source_spans import SPAN_POLICY, indexed_sources, span_schema, expand_references
VERSION='tiered-research-v2-source-ids'
POLICY='''
各質問は記事の検索意図に必要な範囲で回答する。質問文に複数の細目があっても、非必須項目の細部を際限なく追わない。確認できた範囲に回答を限定し、その限定で主要な判断が成立するなら採用する。条件や数値を捏造しない。
存在するかという質問は該当例と条件を示せれば回答可能。全対象の不存在の証明を追加要求しない。
関連公式と補助資料を探索済みの非必須情報は、なお不明なら本稿で使わない理由を記録して省略する。資料抜粋の欠落を非公表・不存在としない。別対象の確認済みの強みは残す。
出典本文がtruncatedの場合は抜粋である。省略部分を推測して引用しない。回答と理由は簡潔に、不要な全プランの列挙や同じ説明の反復を避ける。
'''

def packed_sources(pages,hints):
 copied=copy.deepcopy(pages)
 by_url={p['url']:p for p in copied}
 for item in hints:
  for ref in item.get('evidence',[]):
   if ref.get('url') in by_url:
    by_url[ref['url']].setdefault('evidence_quotes',[]).extend([ref.get('quote',''),ref.get('expert_qualification_quote','')])
 successful=[p for p in copied if p.get('status')=='success' and p.get('text')]
 result=json.loads(source_evidence({'content_text':json.dumps(successful,ensure_ascii=False)},max_chars=60000)) if successful else []
 for p in result:p['status']='success'
 result.extend({'url':p['url'],'status':p.get('status','failed'),'text':'','reason':p.get('reason',''),**({'fetched_at':p['fetched_at']} if p.get('fetched_at') else {})} for p in copied if p not in successful)
 return result

def needs_adjudication(item,question):
 return (not item.get('verified') or item.get('basis') in ('corroborated','expert','historical')
         or question.get('source_requirement')!='standard'
         or any(word in question['question'] for word in ('唯一','最多','最安','No.1'))
         or int(digest(question['id'])[:8],16)%10==0)

def checked_request(job_id,step,request):
 fingerprint=digest(json.dumps(request,ensure_ascii=False,sort_keys=True));old=get_optional_artifact(job_id,step)
 if old and old.get('meta',{}).get('request_sha256')==fingerprint and old.get('meta',{}).get('result_sha256')==digest(old['content_text']):
  print('[tiered] Reuse '+step,flush=True)
  return json.loads(old['content_text']),SimpleNamespace(input_tokens=0,output_tokens=0)
 msg=create_with_retry(None,**request);value=json.loads(response_text(msg));text=json.dumps(value,ensure_ascii=False)
 upsert_artifact(job_id=job_id,step=step,content_type='application/json',content_text=text,
  meta={'request_sha256':fingerprint,'result_sha256':digest(text),'model':request['model'],'input_tokens':msg.usage.input_tokens,'output_tokens':msg.usage.output_tokens})
 return value,msg.usage

def validate_visible_matrix(value,plan,pages):
 from .research_requirements import validate_matrix
 validate_matrix(value,plan,pages)
 # A subsequent check against full documents must not promote citations the
 # model could not see. Only a new response may resolve this failed assertion.
 for item in value['items']:
  if not item.get('verified') and item.get('status')=='confirmed':
   item['status']='unresearched'
 return value


def audit_matrix(job_id,plan,pages,facts,intent_context):
 from .research_requirements import MATRIX_SYSTEM,MATRIX_SCHEMA,validate_matrix
 from .research_verification import COVERAGE_SYSTEM,subject_sources
 records=[get_optional_artifact(job_id,f'research_collection_{i}') for i,_ in enumerate(batches(plan),1)]
 history=[]
 for n in range(1,4):
  old=get_optional_artifact(job_id,f'research_matrix_{n}')
  if old:history.extend(json.loads(old['content_text']).get('items',[]))
 grouped={}
 for q in plan['items']:grouped.setdefault(q['subject'],[]).append(q)
 compact_plan={k:v for k,v in plan.items() if k!='items'}
 compact_plan['items']=[{k:q[k] for k in ('id','subject','question','priority')} for q in plan['items']]
 items=[];inputs=outputs=0
 for index,(subject,questions) in sorted(enumerate(grouped.items(),1),key=lambda entry:entry[1][0]=='共通'):
  notes=[r for r in records if r and r.get('meta',{}).get('subject')==subject]
  ids={q['id'] for q in questions};hints=[i for i in history if i.get('id') in ids]
  urls={u for r in notes for u in r.get('meta',{}).get('source_urls',[])}
  urls.update(u for r in notes for u in extract_urls(r['content_text']))
  if subject=='共通':
   related={q['id'] for name,qs in grouped.items() if name!='共通' and any(name in q['question'] for q in questions) for q in qs}
   hints.extend(i for i in items if i['id'] in related)
  for h in hints:
   urls.update(r['url'] for r in h.get('evidence',[]));urls.update(h.get('official_checked_urls',[]))
  selected=subject_sources(pages,urls) if urls else pages
  packed=packed_sources(selected,hints)
  model_sources,source_index=indexed_sources(packed)
  payload={'article_plan':compact_plan,'plan':{**plan,'items':questions},'sources':model_sources,
           'searches':[r.get('meta',{}).get('search_queries',[]) for r in notes]}
  request=dict(model='gpt-6-luna',max_tokens=18000,system=MATRIX_SYSTEM+POLICY+SPAN_POLICY,output_config=span_schema(MATRIX_SCHEMA),
               messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
  print('[tiered] Screen '+subject,flush=True)
  value,usage=checked_request(job_id,f'tiered_screen_{index}',request);inputs+=usage.input_tokens;outputs+=usage.output_tokens
  value=expand_references(value,source_index)
  validate_visible_matrix(value,{'items':questions},packed)
  planned={q['id']:q for q in questions}
  pending=[i for i in value['items'] if needs_adjudication(i,planned[i['id']])]
  if pending:
   pending_ids={i['id'] for i in pending}
   payload['plan']={**plan,'items':[q for q in questions if q['id'] in pending_ids]}
   payload['candidate_answers']=pending
   request.update(model='gpt-6.1-sol',max_tokens=min(10000,2000+len(pending)*900),system=MATRIX_SYSTEM+POLICY+SPAN_POLICY+'\n一次判定は参考資料。過剰な不合格も検査し、原文で独立に判定する。回答対象はplan.itemsのみ。')
   request['messages']=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
   print('[tiered] Adjudicate '+subject+' '+str(len(pending)),flush=True)
   second,usage=checked_request(job_id,f'tiered_adjudication_{index}',request);inputs+=usage.input_tokens;outputs+=usage.output_tokens
   second=expand_references(second,source_index)
   validate_visible_matrix(second,{'items':payload['plan']['items']},packed)
   replacements={i['id']:i for i in second['items']}
   value['items']=[replacements.get(i['id'],i) for i in value['items']]
  validate_matrix(value,{'items':questions},selected)
  items.extend(value['items'])
 schema={'format':{'type':'json_schema','schema':{'type':'object','properties':{
  'coverage_sufficient':{'type':'boolean'},'coverage_reason':{'type':'string'},'coverage_issues':{'type':'array','items':{'type':'object','properties':{'id':{'type':'string'},'reason':{'type':'string'}},'required':['id','reason'],'additionalProperties':False}}},'required':['coverage_sufficient','coverage_reason','coverage_issues'],'additionalProperties':False}}}
 decisions=[{k:i.get(k) for k in ('id','answer','verified','basis','reason','applicable_at','supports_current_conclusion','omission_reason')} for i in items]
 request=dict(model='gpt-6.1-sol',max_tokens=4000,system=COVERAGE_SYSTEM+POLICY+'\n不合格はcoverage_issuesに既存質問IDと理由を返す。合格時は空配列。',output_config=schema,
  messages=[{'role':'user','content':json.dumps({'plan':compact_plan,'decisions':decisions,'intent_context':intent_context},ensure_ascii=False)}])
 overall,usage=checked_request(job_id,'tiered_coverage',request);inputs+=usage.input_tokens;outputs+=usage.output_tokens
 return {**overall,'items':items},SimpleNamespace(input_tokens=inputs,output_tokens=outputs)
