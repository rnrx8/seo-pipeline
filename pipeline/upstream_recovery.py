"""A recorded dependency queue for new editorial assignments, not error exceptions."""
import copy
import json

from .content_quality import ContentQualityError, digest

VERSION='editorial-recovery-v2-dependencies'


def require_material_refresh(job_id,artifact,question_ids):
    """A new source context is writable only inside its recorded repair step."""
    from . import editorial_recovery as r
    state=json.loads(r.get_artifact(job_id,'editorial_recovery')['content_text'])
    allowed={q for route in state.get('routing_receipt',{}).get('routes',[])
             if route['stage']=='research' for q in route['research_ids']}
    if (state.get('version')!=VERSION or state.get('status')!='running'
            or state.get('current_step')!='outline' or set(question_ids)!=allowed
            or not allowed or state['expected_hashes']['outline']!=digest(artifact['content_text'])
            or state['immutable_signature']!=immutable_signature(job_id)):
        raise ContentQualityError('調査更新と構成修正の記録が一致しません。資料の版を置き換えません。')


def immutable_signature(job_id):
    from . import editorial_recovery as r
    from .editorial_contract import REQUEST_FIELDS
    material={}
    for key in ('research_plan','content_contract','editorial_contract','production_brief',
                'product_context','search_intent','reader_needs','intent_chains','reference_structure'):
        a=r.get_optional_artifact(job_id,key);material[key]=a['content_text'] if a else None
        if key=='production_brief' and a:
            material['assignment']=(a.get('meta') or {}).get('editorial_contract')
    job=r.get_job(job_id)
    return digest(json.dumps([material,{k:job.get(k) for k in (*REQUEST_FIELDS,
        'delivery_type','service_id','cta_id','word_count_setting','high_accuracy_mode')}],
        ensure_ascii=False,sort_keys=True))


def repair_steps(job):
    from .ai import astra_review_enabled
    steps=['outline','structure_guard','research_validation']
    if job.get('service_id') or job.get('cta_id'):steps.append('service_map')
    steps.append('article')
    if job.get('cta_id'):steps.append('cta_inject')
    if not astra_review_enabled():steps.append('review')
    if job.get('high_accuracy_mode'):steps.append('fact_review')
    return steps+['audit']


def execute(job_id,keyword,key,state,api_key):
    from . import editorial_recovery as r
    from . import step_content_audit
    if key=='audit':
        return step_content_audit.run(job_id,keyword,api_key=api_key,
            repair_report=state.pop('article_repair_report',None))
    if key=='route':
        from . import upstream_routing
        from .research_requirements import load_plan,require_authoring_matrix
        report,receipt=upstream_routing.resolve(job_id,r.get_artifact(job_id,'article'),
            r.get_artifact(job_id,'outline'),state['source_report'],load_plan(job_id),
            require_authoring_matrix(job_id),state['round'])
        state.update(routed_report=report,routing_receipt=receipt)
        r.save(job_id,'editorial_routing_'+str(state['round']),receipt)
        return
    if key=='research':
        from .upstream_sources import run
        ids={q for route in state['routing_receipt']['routes'] if route['stage']=='research'
             for q in route['research_ids']}
        return run(job_id,keyword,state['routed_report'],ids,state['round'],api_key)
    if key=='outline':
        from . import step_research_guard
        article=r.get_artifact(job_id,'article');outline=r.get_artifact(job_id,'outline')
        report=copy.deepcopy(state['routed_report'])
        for finding in report['editorial_findings']:
            if finding.get('stage_to_fix')=='research':finding['stage_to_fix']='outline'
        mapped=r.outline_report(article,outline,report)
        if not any(route['stage']=='outline' for route in state['routing_receipt']['routes']):
            # Updating factual instructions is not permission to reparent,
            # reorder or expand chapters. Keep the existing article design.
            mapped['structural_issues']=[]
            mapped['blueprint_issues'][0]['repair_kind']='editorial'
        source_ids={q for route in state['routing_receipt']['routes'] if route['stage']=='research'
                    for q in route['research_ids']}
        # Rechecked facts can affect any section using them, not just the quoted
        # paragraph. The rest of the outline remains read-only.
        if source_ids:
            from .editorial_blueprint import require_projection
            targets={s['id'] for s in require_projection(outline)['sections'] if set(s['research_ids'])&source_ids}
            mapped['blueprint_issues'][0]['section_ids']=sorted(targets|set(mapped['blueprint_issues'][0]['section_ids']))
            mapped['material_refresh_question_ids']=sorted(source_ids)
        for name in ('article','outline','content_audit'):
            value=r.get_artifact(job_id,name)
            r.upsert_artifact(job_id=job_id,step=name+'_before_editorial_recovery',
                content_type=value.get('content_type','text/markdown'),content_text=value['content_text'],meta=value.get('meta') or {})
        return step_research_guard.repair_outline(job_id,keyword,mapped)
    from . import (step_structure_guard,step_research_guard,step_service_map,
                   outline_body_repair,step_cta_inject,step_review,step_fact_review)
    fn={'structure_guard':step_structure_guard.run_before_research,
        'research_validation':step_research_guard.run,'service_map':step_service_map.run,
        'article':outline_body_repair.run,'cta_inject':step_cta_inject.run,
        'review':step_review.run,'fact_review':step_fact_review.run}[key]
    return fn(job_id,keyword,api_key=api_key)


def scope_key(job_id,report):
    from . import editorial_recovery as r
    routed=copy.deepcopy(report)
    for finding in routed['editorial_findings']:
        if finding.get('stage_to_fix')=='research':finding['stage_to_fix']='outline'
    mapped=r.outline_report(r.get_artifact(job_id,'article'),r.get_artifact(job_id,'outline'),routed)
    return digest(json.dumps([mapped['blueprint_issues'][0]['section_ids'],
        sorted({f['check_key'] for f in report['editorial_findings'] if f.get('severity')=='blocking'})]))


def run(job_id,keyword,api_key=None,state=None):
    from . import editorial_recovery as r
    from .content_quality import snapshot,_require_audit_strict
    from .quality_warnings import strict_checks
    from .quality_budget import scope
    state=copy.deepcopy(state) if state else {'version':VERSION,'status':'ready','steps':['audit'],
        'round':0,'history':[],'current_step':None,'input_signature':r.input_signature(job_id,keyword),
        'immutable_signature':immutable_signature(job_id),'expected_hashes':r.current_hashes(job_id)}
    if state.get('version')!=VERSION or state.get('status') not in ('ready','completed'):
        raise r.EditorialRecoveryError('修正要求が実行中または状態未確定です。保存結果を確認するまで再送しません。')
    if state['status']=='completed':
        context=r.audit_context(job_id,keyword)
        report=json.loads(r.get_artifact(job_id,'content_audit')['content_text'])
        _require_audit_strict(report,snapshot(**context),stage='article',context=context)
        if state.get('final_snapshot')!=report['snapshot']:
            raise r.EditorialRecoveryError('完了確認後に原稿または資料が変わっています。')
        return r.get_artifact(job_id,'content_audit')
    while state['steps']:
        if (state['input_signature']!=r.input_signature(job_id,keyword)
                or state['immutable_signature']!=immutable_signature(job_id)
                or state['expected_hashes']!=r.current_hashes(job_id)):
            raise r.EditorialRecoveryError('修正開始前に入力が変更されています。古い要求を送信しません。')
        key=state['steps'][0]
        state.update(status='running',current_step=key);r.save(job_id,'editorial_recovery',state)
        try:
            token=r.AUDIT_RECOVERY.set(key=='audit')
            try:
                with strict_checks(),scope(job_id,'content_audit'):
                    result=execute(job_id,keyword,key,state,api_key)
            finally:r.AUDIT_RECOVERY.reset(token)
            if state['immutable_signature']!=immutable_signature(job_id):
                raise r.EditorialRecoveryError('許可していない設定・計画が修正中に変わりました。')
            if key!='research' and state['input_signature']!=r.input_signature(job_id,keyword):
                raise r.EditorialRecoveryError('対象外の資料が修正中に変わりました。')
            allowed={'route':set(),'research':set(),'outline':{'outline'},
                'structure_guard':{'outline'},'research_validation':{'outline'},'service_map':set(),
                'article':{'article'},'cta_inject':{'article'},'review':{'article'},
                'fact_review':{'article','fresh_sources_review','fact_review_evidence'},
                'audit':{'article','outline'}}[key]
            if any(value!=state['expected_hashes'][name] and name not in allowed
                   for name,value in r.current_hashes(job_id).items()):
                raise r.EditorialRecoveryError('修正工程の範囲外で原稿が変更されています。')
            state['steps'].pop(0)
            if key=='route':
                stages={row['stage'] for row in state['routing_receipt']['routes']}
                if stages<={'article'}:
                    state['article_repair_report']=state['routed_report'];state['steps']=['audit']
                else:
                    state['steps']=(['research'] if 'research' in stages else [])+repair_steps(r.get_job(job_id))
            if key=='audit':
                context=r.audit_context(job_id,keyword)
                report=json.loads(r.get_artifact(job_id,'content_audit')['content_text'])
                _require_audit_strict(report,snapshot(**context),stage='article',context=context)
                state.update(status='completed',current_step=None,steps=[],final_snapshot=report['snapshot'],
                    expected_hashes=r.current_hashes(job_id),input_signature=r.input_signature(job_id,keyword))
                r.save(job_id,'editorial_recovery',state);r.clear_pending_marker(job_id)
                return result
        except r.UpstreamRepairRequired as exc:
            if key!='audit':raise
            report=exc.report
            if report.get('snapshot')!=snapshot(**r.audit_context(job_id,keyword)) or report.get('evidence_pending'):
                raise r.EditorialRecoveryError('現在の本文に対応する確認済み指摘がありません。') from exc
            identity=scope_key(job_id,report)
            count=sum(h['scope_sha256']==identity for h in state['history'])
            if count>=2:
                state.update(status='failed',error='same_scope_unresolved',source_report=report)
                r.save(job_id,'editorial_recovery',state)
                raise r.EditorialRecoveryError('同じ範囲の修正が改善に至りませんでした。原稿・指摘を保持し、同じ課金を繰り返しません。') from exc
            state['round']+=1
            state['history'].append({'scope_sha256':identity,'snapshot':report['snapshot'],'round':state['round']})
            state.update(source_report=report,steps=['route'])
            r.save(job_id,'editorial_recovery_source_'+str(state['round']),report)
        except Exception as exc:
            state.update(status='failed',error_type=type(exc).__name__,error=str(exc))
            r.save(job_id,'editorial_recovery',state)
            raise
        state.update(status='ready',current_step=None,expected_hashes=r.current_hashes(job_id),
                     input_signature=r.input_signature(job_id,keyword))
        r.save(job_id,'editorial_recovery',state)
    raise r.EditorialRecoveryError('修正処理の最終確認がありません。')
