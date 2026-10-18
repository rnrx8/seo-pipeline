"""One normal scoped Opus edit and correct-keyword lead-only recheck."""
import hashlib
import json
import os
from pathlib import Path
import sys

from reader_depth_followup import BACKEND, sha, write
from stage6_replay import capture


def main():
    prior, root = (Path(v).resolve() for v in sys.argv[1:3])
    parent=prior;calls=[];visited=set()
    while parent:
        if str(parent) in visited:raise RuntimeError('Repeated cost history')
        visited.add(str(parent))
        calls += [c for p in (parent/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        manifest=json.loads((parent/'manifest.json').read_text())
        source=Path(manifest['source']) if 'prior_calls_sha256' in manifest else parent
        if manifest.get('previous_trial'):parent=Path(manifest['previous_trial'])
        elif 'prior_calls_sha256' in manifest:parent=source
        else:parent=None
    if any(c['status']=='pending' for c in calls):raise RuntimeError('Pending earlier requests')
    previous_cost=sum(c.get('cost_usd',c['reserved_usd']) for c in calls)
    cap=7.5-previous_cost
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
    pm=json.loads((prior/'manifest.json').read_text());source=Path(pm['source'])
    original=(source/'merged-article-v2.md').read_text();body_start=original.index('\n## ')
    title=original.split('\n',1)[0]
    initial=json.loads((prior/'proposal.json').read_text())['lead']
    text=title+'\n\n'+initial+original[body_start:]
    outline=json.loads((prior/'outline.json').read_text())['content_text']
    normal_source=Path(json.loads((source/'manifest.json').read_text())['source'])
    facts=json.loads((normal_source/'artifacts/fact_sheet.json').read_text())['content_text']
    needs=json.loads(json.loads((Path(pm['hypotheses'])/'analysis_artifacts/reader_needs.json').read_text())['content_text'])['selected']
    need={'id':'d1','role':'primary','scope':'リードのみ','depends_on':[],'research_ids':['q1'],
        'question':'セカンドパートナーを探したい人の気持ちから読む理由まで自然に伝わるか',
        'answer_requirements':['冒頭でセカンドパートナーを探す話と自然に分かる。完全一致のキーワード転記は不要だが、一般的な話し相手探しとの区別がつくこと','受け止めてほしい気持ちとためらいが読者の立場で伝わる','文同士の意味・視点が自然につながり、本文で得られる助けを伝える']}
    feedback='感情を汲み取る文章と自然な文間の流れは明示された要件。180字前後を目安。要素や字数だけで充足としない。'
    def review(lead,name,decision=need,keyword='セカンドパートナー 探し方'):
        scoped=title+'\n\n'+lead if name!='direct' else '# 登録メールが届かないとき\n\n'+lead
        request,design=capture(scoped,decision,'coverage',facts='この検査は導入だけ。新しい数値・機能の断定は不要。',
            extra_requirements={'keyword':keyword,'evaluation_scope':'導入だけを検査。本文・表・商品紹介・まとめの省略は欠陥ではない。',
                'custom_prompt':feedback if name!='direct' else '短い直接操作の回答。感情描写は不要。','measured_lead_chars':len(lead)})
        with qb.scope('lead-coherence-repair','quality'):value,usage=tr.checked_request('lead-coherence-repair',name,request)
        checks=fq.parse_focus(json.dumps({'checks':value['checks']}),fq.ROLES['coverage'],scoped)
        findings=ef.checked_findings(value,checks,scoped)
        checks=ad.checked_result(checks,value,design,scoped)
        r={'valid':all(c['status']!='fail' for c in checks),'checks':checks,'editorial_findings':findings,'lead_review':value.get('lead_review')}
        write(root/(name+'.json'),r)
        return r
    write(root/'manifest.json',{'scope':'One normal lead-only repair; explicit real keyword in scoped QA; not whole article QA',
        'prior_trial':str(prior),'previous_calls_sha256':sha(calls),'previous_partial_usage_usd':previous_cost,
        'this_cap_usd':cap,'approved_partial_cap_usd':7.5,'source_body_raw_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
        'expected':{'before':False,'after':True,'direct':True},'handwritten_lead_examples_in_request':False})
    result={'status':'running'}
    try:
        before=review(initial,'before')
        lead_id='block-0002'
        human={'check_key':'coverage','severity':'blocking','block_ids':[lead_id],
            'problem':'気持ちの流れはあるが、何の相手を探す話か冒頭で分からない。末尾も制作側の説明であり、自然な読者への案内に整える必要がある。',
            'reader_impact':'一般的な相談相手を探す記事とも読め、セカンドパートナーを探す読者が自分向けの導入だと捉えにくい。',
            'required_change':'読者の気持ちとためらいの流れを保ち、冒頭からセカンドパートナーを探す話と自然に分かるようにする。対象属性や記事作業を詰め込まず、本文で得られる助けへつなぐ。比較軸・方法名を羅列しない。',
            'stage_to_fix':'article','origin':'codex_editorial_read'}
        report={**before,'valid':False,'editorial_findings':before['editorial_findings']+[human]}
        if all(c['status']!='fail' for c in report['checks']):
            report['checks'][0].update(status='fail',reason=human['problem'],affected_blocks=[{'id':lead_id,'reason':human['required_change']}])
        write(root/'consolidated-findings.json',report)
        reqs={'keyword':'セカンドパートナー 探し方','custom_prompt':feedback,'intent_value_context':needs}
        request,packet=ca.prepare_content_repair(client,job_id='lead-coherence-repair',text=text,report=report,
            requirements=reqs,sources='[]',facts=facts,contract={},issues=[])
        with qb.scope('lead-coherence-repair','article'):response=measured(client,**request)
        raw=ai.message_text(response)
        candidate,binding=ca.apply_reviewed_edits(text,raw,packet,report,[],facts,outline,None,{},bind_sections(text,outline))
        lead=candidate.split('\n\n',1)[1].split('\n## ',1)[0].strip()
        if candidate[candidate.index('\n## '):]!=original[body_start:]:raise RuntimeError('Non-lead article body changed')
        write(root/'proposal.json',{'lead':lead,'characters':len(lead)})
        (root/'merged-article.md').write_text(candidate)
        after=review(lead,'after')
        direct_text='登録メールが届かないときは、メールアドレスと迷惑メールフォルダを確認し、登録画面から一度再送してください。それでも届かなければサポートへ問い合わせます。'
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
            combined_partial_usage_usd=previous_cost+cost,approved_partial_cap_usd=7.5,
            pending_calls=sum(c['status']=='pending' for c in current),production_changes=False)
        write(root/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
