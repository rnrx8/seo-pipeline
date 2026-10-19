"""One normal scoped Opus edit and correct-keyword lead-only recheck."""
import hashlib
import json
import os
from pathlib import Path
import sys

from reader_depth_followup import BACKEND, sha, write
from stage6_replay import capture
from editorial_reference import load_accepted_reference, reference_feedback


def main():
    prior, root = (Path(v).resolve() for v in sys.argv[1:3])
    accepted_reference=None
    if '--accepted-reference' in sys.argv:
        if '--editorial-reference' in sys.argv:
            raise RuntimeError('Choose one explicit editorial reference')
        accepted_reference=load_accepted_reference(
            sys.argv[sys.argv.index('--accepted-reference')+1], 'セカンドパートナー 探し方')
    transfer_root=Path(sys.argv[sys.argv.index('--lead-transfer-controls')+1]).resolve() if '--lead-transfer-controls' in sys.argv else None
    history_root=Path(sys.argv[sys.argv.index('--cost-history-root')+1]).resolve() if '--cost-history-root' in sys.argv else transfer_root or prior
    parent=history_root;calls=[];visited=set()
    while parent:
        if str(parent) in visited:raise RuntimeError('Repeated cost history')
        visited.add(str(parent))
        calls += [c for p in (parent/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        manifest=json.loads((parent/'manifest.json').read_text())
        source=Path(manifest['source']) if 'source' in manifest else parent
        for value in manifest.get('extra_allowance_history',[]):
            history=Path(value)
            if str(history) in visited:raise RuntimeError('Repeated extra cost history')
            visited.add(str(history))
            calls += [c for p in (history/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        if manifest.get('prior_trial'):parent=Path(manifest['prior_trial'])
        elif manifest.get('previous_trial'):parent=Path(manifest['previous_trial'])
        elif 'prior_calls_sha256' in manifest:parent=source
        else:parent=None
    if any(c['status']=='pending' for c in calls):raise RuntimeError('Pending earlier requests')
    previous_cost=sum(c.get('cost_usd',c['reserved_usd']) for c in calls)
    approved_cap=7.5
    authorization=None
    if '--budget-authorization' in sys.argv:
        authorization=json.loads(Path(sys.argv[sys.argv.index('--budget-authorization')+1]).read_text())
        if (authorization.get('status')!='approved' or authorization.get('previous_cap_usd')!=7.5
            or authorization.get('additional_cap_usd')!=1.5 or not authorization.get('user_reply')):
            raise RuntimeError('Explicit additional budget approval is required')
        approved_cap=authorization['previous_cap_usd']+authorization['additional_cap_usd']
    cap=approved_cap-previous_cost
    if cap<=0:raise RuntimeError('Approved allowance exhausted')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'started.json').open('x') as f:json.dump({'prior_usage_usd':previous_cost,'cap_usd':cap},f)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered',ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused',QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_TOTAL_LIMIT_USD=str(cap))
    sys.path.insert(0,str(BACKEND))
    from pipeline import db,ai,quality_budget as qb,tiered_research as tr
    from pipeline import focused_quality as fq,editorial_findings as ef,article_design as ad
    from pipeline import step_content_audit as ca,content_edits as ce,content_quality as cq
    from pipeline.section_identity import bind_sections
    def forbidden(*a,**kw):raise RuntimeError('Production DB forbidden')
    db._base=db._headers=forbidden
    db.get_optional_artifact=tr.get_optional_artifact=lambda *a:None
    tr.upsert_artifact=lambda **kw:kw
    qb.LIMIT=cap;qb.REVIEW_PHASE_LIMITS={'outline':cap,'article':cap}
    from dotenv import dotenv_values
    values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        if not values.get(key):raise RuntimeError('Existing provider credential unavailable')
        os.environ[key]=values[key]
    import anthropic
    client=anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],max_retries=0)
    normal=ai.create_with_retry;counter=[0]
    def measured(*a,**kw):
        counter[0]+=1;name=f'{counter[0]:04d}.json';write(root/'requests'/name,kw)
        response=normal(*a,**kw)
        write(root/'responses'/name,{'text':ai.message_text(response),'model':response.model,
            'usage':response.usage.model_dump() if hasattr(response.usage,'model_dump') else vars(response.usage)})
        return response
    ai.create_with_retry=tr.create_with_retry=measured
    ca.create_with_retry=measured
    pm=json.loads((prior/'manifest.json').read_text());context_root=prior;context_manifest=pm
    while 'source' not in context_manifest:
        context_root=Path(context_manifest['prior_trial'])
        context_manifest=json.loads((context_root/'manifest.json').read_text())
    source=Path(context_manifest['source'])
    original=(source/'merged-article-v2.md').read_text();body_start=original.index('\n## ')
    title=original.split('\n',1)[0]
    initial=json.loads((prior/'proposal.json').read_text())['lead']
    initial_override=None
    if '--initial-proposal' in sys.argv:
        initial_override=Path(sys.argv[sys.argv.index('--initial-proposal')+1]).resolve()
        initial=json.loads(initial_override.read_text())['lead']
    transfer_controls=None
    if transfer_root:
        transfer_manifest=json.loads((transfer_root/'manifest.json').read_text())
        transfer_result=json.loads((transfer_root/'result.json').read_text())
        if transfer_result.get('status')!='completed' or transfer_result.get('pending_calls')!=0:
            raise RuntimeError('Transfer controls must be complete')
        changed={name for name,digest in transfer_manifest['pipeline_sha256'].items()
                 if hashlib.sha256((BACKEND/'pipeline'/name).read_bytes()).hexdigest()!=digest}
        comparison='--compare-repair-policy' in sys.argv
        if changed and not (comparison and changed=={'step_content_audit.py'}):
            raise RuntimeError('Cannot reuse transfer controls after policy changes')
        if comparison and changed!={'step_content_audit.py'}:
            raise RuntimeError('Repair-policy comparison must change only the repair module')
        transfer_controls={c['id']:c for c in transfer_result['cases']}
        if transfer_controls['relationship']['valid'] or not transfer_controls['direct']['valid']:
            raise RuntimeError('Expected failed relationship lead and passed direct control')
        initial=transfer_controls['relationship']['lead']
    selected_direction=transfer_controls['relationship']['lead_direction'] if transfer_controls else None
    if '--lead-direction-file' in sys.argv:
        selected_direction=json.loads(Path(sys.argv[sys.argv.index('--lead-direction-file')+1]).read_text())['lead_direction']
        if not isinstance(selected_direction,str) or not selected_direction.strip():
            raise RuntimeError('Selected lead direction must be non-empty')
    text=title+'\n\n'+initial+original[body_start:]
    outline=json.loads((context_root/'outline.json').read_text())['content_text']
    normal_source=Path(json.loads((source/'manifest.json').read_text())['source'])
    facts=json.loads((normal_source/'artifacts/fact_sheet.json').read_text())['content_text']
    needs=json.loads(json.loads((Path(context_manifest['hypotheses'])/'analysis_artifacts/reader_needs.json').read_text())['content_text'])['selected']
    need={'id':'d1','role':'primary','scope':'リードのみ','depends_on':[],'research_ids':['q1'],
        'question':'セカンドパートナーを探したい人の気持ちから読む理由まで自然に伝わるか',
        'answer_requirements':['冒頭でセカンドパートナーを探す話と自然に分かる。完全一致のキーワード転記は不要だが、一般的な話し相手探しとの区別がつくこと','受け止めてほしい気持ちとためらいが読者の立場で伝わる','文同士の意味・視点が自然につながり、本文で得られる助けを伝える']}
    feedback='感情を汲み取る文章と自然な文間の流れは明示された要件。180字前後を目安。要素や字数だけで充足としない。'
    if '--lead-style' in sys.argv:
        feedback=Path(context_manifest['request_feedback_path']).read_text().strip()
        feedback+=' 冒頭は記事そのものの内容説明ではなく、主題の具体的な事情や望む変化の話として自然に始める。'
        need['answer_requirements']=['冒頭で何を探す人の話か自然に分かり、記事の自己説明で始めない',
            '具体的な事情や得たい体験から読む理由へ自然につながり、読者全員の心理を決めつけない',
            'アプリの推薦は後続の比較章へ任せ、リードは本文で得られる判断の見通しへつなぐ']
    if '--request-feedback' in sys.argv:
        feedback=Path(sys.argv[sys.argv.index('--request-feedback')+1]).read_text().strip()
    reference=accepted_reference
    if '--editorial-reference' in sys.argv:
        reference=json.loads(Path(sys.argv[sys.argv.index('--editorial-reference')+1]).read_text())
        if not reference.get('reference_text') or not reference.get('scope'):
            raise RuntimeError('Editorial reference needs text and its authorized scope')
    feedback=reference_feedback(feedback,reference)
    def review(lead,name,decision=need,keyword='セカンドパートナー 探し方'):
        scoped=title+'\n\n'+lead if name!='direct' else '# 登録メールが届かないとき\n\n'+lead
        design=None
        if selected_direction and name!='direct':
            design={'version':ad.VERSION,'blueprint_sha256':sha(selected_direction),
                'reader_decisions':[decision],'sections':[],
                'lead_direction':selected_direction}
        request,design=capture(scoped,decision,'coverage',facts='この検査は導入だけ。新しい数値・機能の断定は不要。',
            extra_requirements={'keyword':keyword,'evaluation_scope':'導入だけを検査。本文・表・商品紹介・まとめの省略は欠陥ではない。',
                'custom_prompt':feedback if name!='direct' else '短い直接操作の回答。感情描写は不要。','measured_lead_chars':len(lead)},design=design)
        with qb.scope('lead-coherence-repair','quality'):value,usage=tr.checked_request('lead-coherence-repair',name,request)
        checks=fq.parse_focus(json.dumps({'checks':value['checks']}),fq.ROLES['coverage'],scoped)
        findings=ef.checked_findings(value,checks,scoped)
        checks=ad.checked_result(checks,value,design,scoped)
        r={'valid':all(c['status']!='fail' for c in checks),'checks':checks,'editorial_findings':findings,'lead_review':value.get('lead_review')}
        write(root/(name+'.json'),r)
        return r
    write(root/'manifest.json',{'scope':'One normal lead-only repair; explicit real keyword in scoped QA; not whole article QA',
        'prior_trial':str(history_root),'context_trial':str(prior),
        'baseline_transfer_controls':str(transfer_root) if transfer_root else None,
        'repair_policy_comparison':'--compare-repair-policy' in sys.argv,
        'selected_lead_direction':selected_direction,
        'previous_calls_sha256':sha(calls),'previous_partial_usage_usd':previous_cost,
        'this_cap_usd':cap,'approved_partial_cap_usd':approved_cap,'budget_authorization':authorization,
        'source_body_raw_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
        'expected':{'before':None if '--reuse-repair-controls' in sys.argv else False,'after':True,'direct':True},
        'before_review_reused':bool(transfer_root) or '--reuse-repair-controls' in sys.argv,
        'reused_transfer_controls':str(transfer_root) if transfer_root else None,
        'request_feedback_sha256':sha(feedback),'initial_lead_sha256':sha(initial),
        'initial_override':str(initial_override) if initial_override else None,
        'editorial_reference':reference,'handwritten_lead_examples_in_request':bool(reference),
        'scope_limitation':'Reference-guided revision, not zero-shot generation proof' if reference else None})
    reuse_controls='--reuse-trial-controls' in sys.argv
    if reuse_controls:
        saved=json.loads((prior/'result.json').read_text())
        if saved.get('status')!='completed':raise RuntimeError('Only completed trial controls can be reused')
        if any(hashlib.sha256((BACKEND/'pipeline'/name).read_bytes()).hexdigest()!=digest
               for name,digest in pm['pipeline_sha256'].items()):
            raise RuntimeError('Control policy changed; cannot reuse')
        controls={c['id']:c for c in saved['controls']}
        if controls['b']['actual']!='fail' or controls['c']['actual']!='pass':
            raise RuntimeError('Expected failed generated lead and passed direct control')
    reuse_repair='--reuse-repair-controls' in sys.argv
    if reuse_repair:
        if initial_override:raise RuntimeError('Changed initial text cannot reuse its before review')
        saved=json.loads((prior/'result.json').read_text())
        if saved.get('status')!='completed' or saved.get('pending_calls')!=0:
            raise RuntimeError('Only completed repair controls can be reused')
        if any(hashlib.sha256((BACKEND/'pipeline'/name).read_bytes()).hexdigest()!=digest
               for name,digest in pm['pipeline_sha256'].items()):
            raise RuntimeError('Control policy changed; cannot reuse repair controls')
    reuse_direct='--reuse-direct-control' in sys.argv
    if reuse_direct:
        saved=json.loads((prior/'result.json').read_text())
        if (saved.get('status')!='completed' or saved.get('pending_calls')!=0
            or not json.loads((prior/'direct.json').read_text())['valid']
            or any(hashlib.sha256((BACKEND/'pipeline'/name).read_bytes()).hexdigest()!=digest
                   for name,digest in pm['pipeline_sha256'].items())):
            raise RuntimeError('Only unchanged, completed direct control can be reused')
    result={'status':'running'}
    try:
        before=({k:transfer_controls['relationship'][k] for k in ('valid','checks','editorial_findings','lead_review')} if transfer_controls else {'valid':False,**{k:controls['b'][k] for k in ('checks','editorial_findings','lead_review')},
                 'reused_control':str(prior/'result.json')+'#b'} if reuse_controls else json.loads((prior/'after.json').read_text()) if reuse_repair else review(initial,'before'))
        if reuse_repair:
            before=json.loads((prior/'after.json').read_text())
            before['reused_control']=str(prior/'after.json')
        if transfer_controls:before['reused_control']=str(transfer_root/'result.json')+'#relationship'
        if transfer_controls or reuse_controls or reuse_repair:write(root/'before.json',before)
        lead_id='block-0002'
        human={'check_key':'coverage','severity':'blocking','block_ids':[lead_id],
            'problem':'気持ちの流れはあるが、何の相手を探す話か冒頭で分からない。末尾も制作側の説明であり、自然な読者への案内に整える必要がある。',
            'reader_impact':'一般的な相談相手を探す記事とも読め、セカンドパートナーを探す読者が自分向けの導入だと捉えにくい。',
            'required_change':'読者の気持ちとためらいの流れを保ち、冒頭からセカンドパートナーを探す話と自然に分かるようにする。対象属性や記事作業を詰め込まず、本文で得られる助けへつなぐ。比較軸・方法名を羅列しない。',
            'stage_to_fix':'article','origin':'codex_editorial_read'}
        if '--reader-address' in sys.argv:
            human.update(problem='冒頭が誰の望みか不明な独白で始まり、セカンドパートナーを探す読者へ案内する文章として成立していない。',
                reader_impact='書き手が突然自己紹介しているように読め、自分向けの記事か分からない。',
                required_change='書き手から対象読者へ案内する視点を保ち、冒頭から何を探す人の話か自然に伝える。読者の内面を無主語の告白にせず、短い気持ちとためらいから本文の助けへつなぐ。固定の呼びかけや例文の使用は要求しない。')
        if '--lead-style' in sys.argv:
            human.update(problem='主題は分かるが、冒頭が記事そのものの内容説明になり、次文も人物像の解説へ移っている。具体的な事情から読者へ語りかける導入になっていない。',
                reader_impact='何についての記事かという案内しか得られず、自分の生活や望みに関わる話として読む動機が弱い。',
                required_change='記事そのものの紹介から入らず、主題が自然に分かる具体的な事情や得たい体験から始める。主題と読者への視点を保ち、抽象的な感情の説明だけで済ませない。本文で得られる判断へつなぎ、アプリの推薦は本文へ任せる。例文や決まった書き出しは要求しない。')
        if '--editorial-finding' in sys.argv:
            human=json.loads(Path(sys.argv[sys.argv.index('--editorial-finding')+1]).read_text())
            if human.get('block_ids')!=[lead_id] or human.get('stage_to_fix')!='article':
                raise RuntimeError('Editorial finding must target this lead')
        report={**before,'valid':False,'editorial_findings':before['editorial_findings']+[human]}
        if all(c['status']!='fail' for c in report['checks']):
            report['checks'][0].update(status='fail',reason=human['problem'],affected_blocks=[{'id':lead_id,'reason':human['required_change']}])
        write(root/'consolidated-findings.json',report)
        reqs={'keyword':'セカンドパートナー 探し方','custom_prompt':feedback,'intent_value_context':needs}
        if selected_direction:
            reqs['article_design']={'version':ad.VERSION,'blueprint_sha256':sha(selected_direction),
                'reader_decisions':[need],'sections':[],
                'lead_direction':selected_direction}
        request,packet=ca.prepare_content_repair(client,job_id='lead-coherence-repair',text=text,report=report,
            requirements=reqs,sources='[]',facts=facts,contract={},issues=[])
        with qb.scope('lead-coherence-repair','article'):response=measured(client,**request)
        raw=ai.message_text(response)
        candidate,binding=ca.apply_reviewed_edits(text,raw,packet,report,[],facts,outline,None,{},bind_sections(text,outline))
        lead=candidate.split('\n\n',1)[1].split('\n## ',1)[0].strip()
        if reference and reference.get('preserve_prefix') and not lead.startswith(reference['reference_text']):
            raise RuntimeError('Preferred opening was changed despite the scoped reference')
        if candidate[candidate.index('\n## '):]!=original[body_start:]:raise RuntimeError('Non-lead article body changed')
        write(root/'proposal.json',{'lead':lead,'characters':len(lead)})
        (root/'merged-article.md').write_text(candidate)
        after=review(lead,'after')
        direct_text='登録メールが届かないときは、メールアドレスと迷惑メールフォルダを確認し、登録画面から一度再送してください。それでも届かなければサポートへ問い合わせます。'
        if transfer_controls:
            direct={k:transfer_controls['direct'][k] for k in ('valid','checks','editorial_findings','lead_review')}
            direct['reused_control']=str(transfer_root/'result.json')+'#direct';write(root/'direct.json',direct)
        elif reuse_controls:
            direct={'valid':True,**{k:controls['c'][k] for k in ('checks','editorial_findings','lead_review')},
                    'reused_control':str(prior/'result.json')+'#c'}
            write(root/'direct.json',direct)
        elif reuse_repair or reuse_direct:
            direct=json.loads((prior/'direct.json').read_text())
            direct['reused_control']=str(prior/'direct.json');write(root/'direct.json',direct)
        else:
            direct=review(direct_text,'direct',{**need,'question':'登録メールが届かないとき何をするか','answer_requirements':['確認・再送・未解決時の連絡先が分かる。心理描写は不要。']},'登録メール 届かない')
        result.update(status='completed',before_qa_valid=before['valid'],after_qa_valid=after['valid'],direct_qa_valid=direct['valid'],
            normal_scoped_edit_applied=True,body_unchanged=True,lead_chars=len(lead),
            codex_editorial_acceptance=False,user_review='pending',full_article_qa=False)
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        current=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        cost=sum(c.get('cost_usd',c['reserved_usd']) for c in current)
        result.update(new_usage_usd=cost,previous_partial_usage_usd=previous_cost,
            combined_partial_usage_usd=previous_cost+cost,approved_partial_cap_usd=approved_cap,
            pending_calls=sum(c['status']=='pending' for c in current),production_changes=False)
        write(root/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
