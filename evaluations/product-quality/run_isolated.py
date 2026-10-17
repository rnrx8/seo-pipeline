"""Run the ordinary stage plan on an empty, local-only artifact store.

No normal DB writes, notifications, WordPress, deployment or input rewriting.
This proves pipeline behavior, not the production UI/DB revision implementation.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

BACKEND=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BACKEND))


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    def sdk_value(value):
        if hasattr(value,'model_dump'):return value.model_dump(mode='json')
        raise TypeError('Unsupported recorded value: '+type(value).__name__)
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=sdk_value))
    temp.replace(path)


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def main():
    sys.stdout.reconfigure(line_buffering=True)
    root=Path(sys.argv[1]).resolve()
    settings=json.loads((root/'settings.json').read_text())
    live='--live' in sys.argv
    job=copy.deepcopy(settings['source_job'])
    manifest_path=root/'manifest.json'
    if not manifest_path.exists():
        job.update(id=str(uuid.uuid4()),status='queued',current_step=None,error_message='')
        # No artificial per-stage 250-yen ceiling. This whole-run cap applies
        # solely to this declared trial and never changes production defaults.
        manifest={'job':job,'settings_sha256':sha(settings),'cap_usd':float(sys.argv[sys.argv.index('--cap-usd')+1]) if '--cap-usd' in sys.argv else 12.5,'jpy_per_usd_for_planning':200,
            'scope':'normal pipeline plan, fresh collection and models, local artifact adapter; not browser UI/production DB E2E',
            'serp_provider':'serper' if '--serper' in sys.argv else 'serpapi','revision_tracking':'not enabled; local immutable save history is diagnostic only',
            'model_settings':{'ARTICLE_REVIEW_PROVIDER':'tiered','ARTICLE_WRITING_FLOW':'sonnet_opus','ARTICLE_GENERATION_MODEL':'claude-opus-5-5','QUALITY_RESEARCH_ROUTING':'focused'}}
        dump(manifest_path,manifest)
    manifest=json.loads(manifest_path.read_text())
    if manifest['settings_sha256']!=sha(settings):raise RuntimeError('Frozen settings changed')
    job=copy.deepcopy(manifest['job'])
    os.environ.update(**manifest['model_settings'],QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_BUDGET_MODE='bounded',QUALITY_TOTAL_LIMIT_USD=str(manifest['cap_usd']),SERP_PROVIDER=manifest['serp_provider'],ARTICLE_REVISION_TRACKING='0')
    if '--recover-serp' in sys.argv:
        previous=json.loads((root/'status.json').read_text())
        if previous['status']!='stopped' or previous['error_type']!='SerpQualityError' or previous['paid_calls']!=0 or previous['artifacts']:
            raise RuntimeError('Only the known empty-store SERP failure can recover here')
        os.environ['SERP_PROVIDER']='serper'
    os.environ.pop('QUALITY_COMPLETION_EVAL',None)
    for key in ('SUPABASE_KEY','SUPABASE_URL'):os.environ.pop(key,None)
    if live:
        from dotenv import dotenv_values
        values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
        for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','SERPAPI_KEY','SERPER_API_KEY'):
            if not values.get(key):raise RuntimeError('Existing provider credential unavailable: '+key)
            os.environ[key]=values[key]
    from pipeline import db
    def forbidden(*args,**kwargs):raise RuntimeError('External database access is forbidden in this evaluation')
    db._base=db._headers=forbidden
    artifacts=root/'artifacts';artifacts.mkdir(exist_ok=True)
    def optional(job_id,step):
        if job_id!=job['id']:raise RuntimeError('Unexpected job')
        path=artifacts/(step+'.json')
        return json.loads(path.read_text()) if path.exists() else None
    def get(job_id,step):
        saved=optional(job_id,step)
        if saved is None:raise ValueError('Artifact not found: '+step)
        return saved
    counter=[max([int(p.name.split('-')[0]) for p in (root/'history').glob('*.json')]+[0])]
    def save(job_id,step,content_type,content_text,content=None,payload=None,meta=None):
        if job_id!=job['id']:raise RuntimeError('Unexpected job')
        previous=optional(job_id,step) or {}
        record={**previous,'id':previous.get('id') or str(uuid.uuid4()),'job_id':job_id,'step':step,'content_type':content_type,'content_text':content_text}
        for key,value in (('content',content),('payload',payload),('meta',meta)):
            if value is not None:record[key]=copy.deepcopy(value)
        record.setdefault('meta',{})
        counter[0]+=1
        dump(root/'history'/f'{counter[0]:05d}-{step}.json',record)
        dump(artifacts/(step+'.json'),record)
        return copy.deepcopy(record)
    def update(job_id,**values):
        if job_id!=job['id']:raise RuntimeError('Unexpected job')
        job.update(values);dump(root/'job.json',job)
    db.get_optional_artifact,db.get_artifact,db.upsert_artifact=optional,get,save
    db.get_job=lambda job_id:copy.deepcopy(job) if job_id==job['id'] else forbidden()
    db.insert_job=forbidden
    db.update_job_status=lambda job_id,status:update(job_id,status=status)
    db.update_job_step=lambda job_id,step:update(job_id,current_step=step)
    db.update_job_error=lambda job_id,error_message:update(job_id,error_message=error_message)
    db.update_job_word_count_setting=lambda job_id,word_count_setting:update(job_id,word_count_setting=word_count_setting)
    db.get_service_by_id=lambda ident:next((copy.deepcopy(s) for s in settings.get('services',[]) if s['id']==ident),None)
    db.get_cta_by_id=lambda ident:next((copy.deepcopy(s) for s in settings.get('cta_blocks',[]) if s['id']==ident),None)
    db.get_primary_sources=lambda *args:copy.deepcopy(settings.get('primary_sources',[]))
    db.get_primary_sources_by_preset=lambda *args:copy.deepcopy(settings.get('primary_sources_by_preset',[]))
    db.get_company_settings=lambda *args:copy.deepcopy(settings.get('company_settings',[]))
    db.get_learned_style_rules=lambda *args:copy.deepcopy(settings.get('learned_rules',[]))
    db.get_cached_product_context=lambda *args:None
    db.get_job_company_restriction=lambda *args:job.get('company_restriction') or 'ai'
    db.get_user_email=lambda *args:'nishi.aworks@gmail.com'
    db.get_user_credits_total=lambda *args:0
    from pipeline import quality_budget as qb
    qb.LIMIT=manifest['cap_usd']
    qb.REVIEW_PHASE_LIMITS={'outline':manifest['cap_usd'],'article':manifest['cap_usd']}
    from pipeline import ai
    normal_create=ai.create_with_retry
    request_counter=[max([int(p.stem) for p in (root/'requests').glob('*.json')]+[0])]
    def measured(*args,**kwargs):
        request_counter[0]+=1;ident=f'{request_counter[0]:04d}'
        fingerprint=sha(kwargs)
        blocked=root/'api-contract-recovery.json'
        if blocked.exists() and fingerprint in json.loads(blocked.read_text()).get('blocked_original_request_hashes',[]):
            raise RuntimeError('Original failed request cannot be resent unchanged')
        dump(root/'requests'/(ident+'.json'),kwargs)
        result=normal_create(*args,**kwargs)
        dump(root/'responses'/(ident+'.json'),{'model':getattr(result,'model',kwargs['model']),'stop_reason':result.stop_reason,
            'text':ai.message_text(result),'provider_response':result.model_dump(mode='json') if hasattr(result,'model_dump') else None,'usage':result.usage.model_dump() if hasattr(result.usage,'model_dump') else vars(result.usage)})
        return result
    ai.create_with_retry=measured
    from pipeline import serp_sources
    normal_serp=serp_sources.fetch_serp
    def observed_serp(keyword):
        try:
            data,source=normal_serp(keyword)
            dump(root/'serp-response.json',{'data':data,'source':source})
            return data,source
        except (serp_sources.SerpQualityError,serp_sources.SerpConfigurationError) as exc:
            dump(root/'serp-error.json',{'error_type':type(exc).__name__,'error':str(exc),'provider':os.environ['SERP_PROVIDER']})
            raise
    serp_sources.fetch_serp=observed_serp
    from pipeline.step_plan import build_step_plan
    steps=build_step_plan(job)
    plan={'stages':[k for k,_ in steps], 'models':{s:ai.get_step_config(s) for s in (*ai.STEP_CONFIG,'content_audit','content_repair')},
        'production_writes':False,'input_settings_sha256':sha(settings),'cap_usd':manifest['cap_usd']}
    plan['serp_provider']=os.environ['SERP_PROVIDER']
    dump(root/'plan.json',plan)
    resume_from=sys.argv[sys.argv.index('--resume-from')+1] if '--resume-from' in sys.argv else None
    if resume_from and live:
        saved_manifest=root/'backend-source-manifest.json'
        if saved_manifest.exists():dump(root/('backend-source-manifest-before-'+resume_from+'.json'),json.loads(saved_manifest.read_text()))
    dump(root/'backend-source-manifest.json',{'files':{str(p.relative_to(BACKEND)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))}})
    if not live:
        print(json.dumps(plan,ensure_ascii=False));return
    marker='started.json'
    if '--resume-unstarted' in sys.argv:
        previous=json.loads((root/'status.json').read_text())
        if (previous['status']!='stopped' or previous['paid_calls']!=0 or previous['artifacts']
                or previous['error']!='上限付き資料検索にはSERPER_API_KEYが必要です。高額な内蔵検索へ自動切替しません。'):
            raise RuntimeError('Only the known zero-call preflight failure can resume here')
        dump(root/'preflight-failure.json',previous)
        marker='resume-unstarted.json'
    if '--recover-serp' in sys.argv:
        dump(root/'serp-failure.json',previous)
        dump(root/'serp-recovery.json',{'reason':'Switch existing provider after a failed acquisition; zero model calls and empty artifact store', 'previous_provider':'serpapi','new_provider':'serper'})
        marker='recover-serp.json'
    if resume_from:
        previous=json.loads((root/'status.json').read_text())
        special_recovery='--resume-after-api-contract-fix' in sys.argv
        if previous['status']!='stopped' or (previous.get('unsettled_calls')!=0 and not special_recovery) or resume_from not in ('research_plan','fact_sheet','research_completeness'):
            raise RuntimeError('Only a settled planning/collection stop can resume here')
        if not get(job['id'],'search_intent') or not json.loads(get(job['id'],'reader_needs')['content_text']).get('selected'):
            raise RuntimeError('Complete unchanged intent is required')
        if special_recovery:
            if resume_from!='research_completeness' or previous['error_type']!='BadRequestError':raise RuntimeError('Unexpected recovery stage')
            calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text()).get('calls',[])]
            uncertain=[c for c in calls if c.get('model')!='search' and c['status']!='accounted']
            if len(uncertain)!=2 or any(c['status']!='unknown_cost_reserved' for c in uncertain):raise RuntimeError('Unresolved requests differ from diagnosed failure')
            incomplete=[c for c in uncertain if c.get('diagnostics',{}).get('incomplete_reason')=='max_output_tokens']
            rejected=[c for c in uncertain if c['model']=='claude-opus-5-5' and c['stage']=='outline']
            failure=json.loads(get(job['id'],'pipeline_failure')['content_text'])
            if len(incomplete)!=1 or len(rejected)!=1 or 'maxItems' not in failure.get('detail','') or '400' not in failure.get('detail',''):raise RuntimeError('No confirmed incomplete/rejected request diagnosis')
            old_requests=[json.loads((root/'requests'/name).read_text()) for name in ('0032.json','0033.json')]
            if old_requests[0]['max_tokens']!=7400 or old_requests[0]['model']!='gpt-6.1-sol':raise RuntimeError('Wrong incomplete request')
            dump(root/'api-contract-recovery.json',{'previous':previous,'blocked_original_request_hashes':[sha(r) for r in old_requests],
                'known_outcomes':['OpenAI response.incomplete max_output_tokens','Anthropic HTTP400 unsupported maxItems'],
                'in_flight_requests':0,'usage_retrieval':'404 for original store=false response; original expense reservations retained',
                'expense_reservations_retained_usd':sum(c['reserved_usd'] for c in uncertain),
                'request_change':'Claim-level output allowance and supported schema projection; no identical failed request resend; no verdict promoted'})
        if resume_from in ('fact_sheet','research_completeness'):
            from pipeline.research_requirements import load_plan
            load_plan(job['id'])
        dump(root/('scope-contract-resume.json' if resume_from=='research_plan' else 'api-contract-resume.json' if special_recovery else 'recorder-resume.json'),{'previous':previous,'resume_from':resume_from,'reason':'Separate analysis proposals from reviewed scope' if resume_from=='research_plan' else 'Correct known API schema/output-capacity failures; retain unknown expense reservations; block unchanged failed requests' if special_recovery else 'Serialize SDK content in local recorder; prior model response is accounted, no uncertain request retried','no_prior_verdict_promoted':True})
        marker='resume-scope-contract.json' if resume_from=='research_plan' else 'resume-api-contract.json' if special_recovery else 'resume-recorder.json'
    with (root/marker).open('x') as file:json.dump({'pid':os.getpid(),'at':time.time()},file)
    if not resume_from and list(artifacts.glob('*.json')):raise RuntimeError('Fresh trial requires an empty artifact store')
    # API and stage requests are recorded before sending; an interrupted unknown
    # reservation must be reconciled, never silently rerun by this fresh runner.
    status={'status':'running','job_id':job['id'],'normal_plan':plan['stages'],'started_at':time.time()}
    dump(root/'status.json',status)
    from pipeline.runner import run_pipeline
    try:
        if resume_from:
            # Use the existing server-side resume scheduler. External incident
            # delivery is a local receipt in this evaluation, never a send.
            from pipeline import notify,autofix
            def incident(*args,**kwargs):
                dump(root/'local-incident.json',{'args':args,'kwargs':kwargs,'sent':False})
            notify.alert_failed_job=notify.alert_stale_job=notify.alert_quality_warning=incident
            autofix.create_github_issue=incident
            import api_server
            api_server._run_pipeline(job['id'],job['main_keyword'],resume_from=resume_from)
        else:
            run_pipeline(job['main_keyword'],job_id=job['id'])
        status['runner_status']=job['status']
        # A normal 'done' can contain warn-and-continue results. Never accept it
        # as semantic completion without strict current-revision checks.
        from pipeline.research_requirements import _require_matrix_strict
        from pipeline.content_quality import _require_audit_strict,snapshot,source_evidence,audit_facts,final_review_requirements
        _require_matrix_strict(job['id'])
        article=get(job['id'],'article');outline=get(job['id'],'outline')['content_text']
        ctx=dict(text=article['content_text'],facts=audit_facts(get(job['id'],'fact_sheet')['content_text'],article,high_accuracy=False),outline=outline,
            contract=json.loads(get(job['id'],'content_contract')['content_text']),requirements=final_review_requirements(job,job['main_keyword']),sources=source_evidence(get(job['id'],'fresh_sources')))
        _require_audit_strict(json.loads(get(job['id'],'content_audit')['content_text']),snapshot(**ctx),stage='article',context=ctx)
        final=json.loads(get(job['id'],'structure_validation_final')['content_text'])
        if not final.get('valid'):raise ValueError('Final structure is not valid')
        status.update(status='completed_current_policy_checks',human_semantic_review='pending',ui_e2e='not_run')
    except Exception as exc:
        from pipeline.content_quality import ContentQualityError
        status.update(status='stopped',error_type=type(exc).__name__,error=str(exc) if isinstance(exc,(ContentQualityError,ValueError,serp_sources.SerpQualityError,serp_sources.SerpConfigurationError)) else type(exc).__name__,runner_status=job['status'])
        print('STOPPED',status['error_type'],status['error'],flush=True)
    finally:
        calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text()).get('calls',[])]
        model_calls=[c for c in calls if c.get('model')!='search']
        search_calls=[c for c in calls if c.get('model')=='search']
        status.update(finished_at=time.time(),paid_calls=len(model_calls),source_search_calls=len(search_calls),
            model_usage_cost_usd=sum(c.get('cost_usd',0) for c in model_calls if c['status']=='accounted'),
            source_search_estimate_usd=sum(c.get('estimated_cost_usd',c['reserved_usd']) for c in search_calls),
            cost_usd=sum(c.get('cost_usd',c['reserved_usd']) for c in calls),
            unsettled_calls=sum(c['status']!='accounted' for c in model_calls),
            pending_search_calls=sum(c['status']=='pending' for c in search_calls),
            initial_serp_search_cost='not metered by model ledger; search provider credits not independently verified',
            artifacts={p.stem:sha(json.loads(p.read_text())) for p in artifacts.glob('*.json')})
        dump(root/'status.json',status);dump(root/'job.json',job)
        print(json.dumps({k:v for k,v in status.items() if k!='artifacts'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
