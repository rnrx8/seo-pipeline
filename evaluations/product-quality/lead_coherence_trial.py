"""Lead-only blueprint/writer proof and blind old/new/direct-answer controls.

Consumes only the remainder of the already approved partial-revision allowance.
No handwritten lead examples enter generation; no production DB or publication.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

from reader_depth_followup import BACKEND, sha, write
from stage6_replay import capture


def main():
    sys.stdout.reconfigure(line_buffering=True)
    source, hypotheses, root = (Path(v).resolve() for v in sys.argv[1:4])
    prior_calls = [c for p in (source/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    previous_trial = None
    if '--previous-trial' in sys.argv:
        previous_trial=Path(sys.argv[sys.argv.index('--previous-trial')+1]).resolve()
        previous_manifest=json.loads((previous_trial/'manifest.json').read_text())
        if previous_trial in (root,source) or previous_manifest['source']!=str(source):
            raise RuntimeError('Previous trial belongs to another allowance')
        prior_calls += [c for p in (previous_trial/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    if any(c['status']=='pending' for c in prior_calls):
        raise RuntimeError('Prior partial revision still has a pending request')
    prior_cost = sum(c.get('cost_usd',c['reserved_usd']) for c in prior_calls)
    cap = 7.5 - prior_cost
    if cap <= 0:
        raise RuntimeError('No approved remainder')
    root.mkdir(parents=True,exist_ok=True)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered', ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused',QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_TOTAL_LIMIT_USD=str(cap))
    sys.path.insert(0,str(BACKEND))
    from pipeline import db, ai, quality_budget as qb, editorial_blueprint as bp
    from pipeline import step_outline, step_article, lead_review as lr, content_quality as cq
    from pipeline import focused_quality as fq, editorial_findings as ef, article_design as ad
    from pipeline import tiered_research as tr
    def forbidden(*a,**kw):
        raise RuntimeError('Production DB forbidden')
    db._base = db._headers = forbidden
    def get(job,step):
        p=root/'local_artifacts'/(step+'.json')
        return json.loads(p.read_text()) if p.exists() else None
    def save(**kw):
        write(root/'local_artifacts'/(kw['step']+'.json'),kw)
        return kw
    db.get_optional_artifact = tr.get_optional_artifact = get
    db.upsert_artifact = tr.upsert_artifact = save
    qb.LIMIT = cap
    qb.REVIEW_PHASE_LIMITS={'outline':cap,'article':cap}
    outline=json.loads((source/'outline.json').read_text())
    old_design=bp.require_projection(outline)
    context=outline['meta']['blueprint_context']
    article=(source/'merged-article-v2.md').read_text()
    start=article.index('\n## ')
    title,old_lead=article[:start].strip().split('\n\n',1)
    needs=json.loads(json.loads((hypotheses/'analysis_artifacts/reader_needs.json').read_text())['content_text'])['selected']
    feedback='主題が自然に分かる短いリード。読者の気持ちを汲み取る文章と、文同士の自然な流れが明示された必須要件。望みやためらいを外側から説明するだけでなく、何を相手に受け止めてもらいたくて、何を打ち明けにくくて、この探し方を知りたくなるのかを読者の立場で伝える。一般的な比較条件の説明へ急がず、その気持ちから記事を読む理由まで一続きに表す。元275字の約3分の2、180字前後が目安。'
    manifest={'scope':'Lead-only design, generation and scoped QA; not normal full generation or full article acceptance',
        'source':str(source),'hypotheses':str(hypotheses),'previous_trial':str(previous_trial) if previous_trial else None,'prior_calls_sha256':sha(prior_calls),
        'prior_usage_usd':prior_cost,'approved_partial_cap_usd':7.5,'this_remainder_cap_usd':cap,
        'source_outline_sha256':sha(outline),'source_article_raw_sha256':hashlib.sha256(article.encode()).hexdigest(),
        'reader_hypotheses_sha256':sha(needs),'no_handwritten_lead_example_in_generation':True,
        'old_lead_in_generation':False,'lead_review_version':lr.VERSION,
        'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'preregistered_controls':{'a':'fail','b':'pass','c':'pass',**({'d':'fail'} if previous_trial else {})},'production_changes':False}
    if (root/'manifest.json').exists():
        saved_manifest=json.loads((root/'manifest.json').read_text())
        if saved_manifest!=manifest:
            saved_result=json.loads((root/'result.json').read_text()) if (root/'result.json').exists() else {}
            old_without_runner={k:v for k,v in saved_manifest.items() if k!='runner_sha256'}
            new_without_runner={k:v for k,v in manifest.items() if k!='runner_sha256'}
            if ('--resume-output-limit' not in sys.argv or old_without_runner!=new_without_runner
                or saved_result.get('status')!='stopped' or saved_result.get('error_type')!='ContentQualityError'
                or 'response.incomplete: 出力上限' not in saved_result.get('error','')
                or saved_result.get('pending_calls')!=0):
                raise RuntimeError('Frozen trial context changed; do not reuse or re-send')
            receipt=root/'output-limit-resume.json'
            with receipt.open('x') as f:
                json.dump({'previous_result':saved_result,'previous_manifest':saved_manifest,
                    'current_runner_sha256':manifest['runner_sha256'],'generation_response_reused_by_request_hash':True,
                    'change':'Lead design QA max_tokens 2500 -> normal 6000; all expense and input/policy hashes retained'},f,ensure_ascii=False,indent=2)
    else:
        write(root/'manifest.json',manifest)
    existing=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    if any(c['status']=='pending' for c in existing):
        raise RuntimeError('Trial has pending request; do not duplicate')
    from dotenv import dotenv_values
    values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for key in ('ANTHROPIC_API_KEY','OPENAI_API_KEY'):
        if not values.get(key):raise RuntimeError('Existing provider credential unavailable')
        os.environ[key]=values[key]
    import anthropic
    client=anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],max_retries=0)
    normal=ai.create_with_retry
    def measured(*args,**kw):
        key=sha(kw);rp=root/'requests'/(key+'.json');sp=root/'responses'/(key+'.json')
        if sp.exists():
            if json.loads(rp.read_text())!=kw:raise RuntimeError('Saved request mismatch')
            v=json.loads(sp.read_text())
            return NS(model=v['model'],stop_reason=v['stop_reason'],usage=NS(**v['usage']),
                content=[NS(type='text',text=v['text'])])
        write(rp,kw)
        response=normal(*args,**kw)
        write(sp,{'model':response.model,'stop_reason':response.stop_reason,
            'usage':response.usage.model_dump() if hasattr(response.usage,'model_dump') else vars(response.usage),
            'text':ai.message_text(response)})
        return response
    ai.create_with_retry=tr.create_with_retry=measured
    job='lead-coherence-trial'
    result={'status':'running','controls':[]}
    try:
        # Same outline policy and edit validation; the overloaded former lead
        # direction is withheld rather than carried into a "new" generation.
        outline_input={'scope':'リードの設計だけを更新。edits/delete_ids/section_orderは空配列。完成リードや例文は作らない。',
            'request':feedback,'reader_hypotheses':needs,
            'article_purpose':'既婚者専用マッチングから既婚者クラブの無料登録につなぐ。主回答は6方法の比較。',
            'article_contents':[{k:s[k] for k in ('title','question','conclusion')} for s in old_design['sections']]}
        request=dict(model=step_outline.MODEL,max_tokens=2500,system=step_outline.SYSTEM_PROMPT+'\n'+bp.POLICY,
            output_config=bp.repair_config(allow_lead=True),messages=[{'role':'user','content':json.dumps(outline_input,ensure_ascii=False)}])
        with qb.scope(job,'outline'):response=measured(client,**request)
        patch=json.loads(ai.message_text(response))
        design=bp.apply_edits(old_design,patch,context,structural=False,targets=[],allow_lead=True)
        revised={'content_text':bp.render(design,context),'meta':bp.metadata(design,context)}
        bp.require_projection(revised,context)
        write(root/'outline.json',revised)
        blocks=cq.content_blocks(revised['content_text']);ids=bp.lead_block_ids(revised)
        check=dict(model='gpt-6.1-sol',max_tokens=6000,system=cq.AUDIT_SYSTEM+'\n'+lr.POLICY+'\nリード設計のみを確認。checksはcoverageのみ。完成リードはまだなく、他の章や根拠の再監査は対象外。',
            output_config=lr.output_config(cq.audit_output_config(('coverage',),locations=True)),
            messages=[{'role':'user','content':json.dumps({'outline_blocks':[b for b in blocks if b['id'] in ids],
                'lead_direction':design['lead_direction'],'requirements':feedback},ensure_ascii=False)}])
        with qb.scope(job,'quality'):verdict,usage=tr.checked_request(job,'lead_design_review',check)
        rows=lr.checked(verdict,ids)
        checks=fq.parse_focus(json.dumps({'checks':verdict['checks']}),('coverage',),revised['content_text'])
        ready=all(c['status']=='pass' for c in checks) and all(v['status']!='fail' for v in rows.values())
        write(root/'lead-design-review.json',{'valid':ready,'raw_verdict':verdict})
        if not ready:raise RuntimeError('Lead design did not pass; no writer request sent')
        writer_data={'scope':'リードだけを1案、JSONのleadへ出力する。H1や見出しは書かない。',
            'request':feedback,'lead_direction':design['lead_direction'],'reader_hypotheses':needs,
            'article_title':title.lstrip('# '),'article_body_for_answer_scope':article[start:],
            'article_purpose':outline_input['article_purpose']}
        request=dict(model=step_article.MODEL,max_tokens=1200,system=step_article.SYSTEM_PROMPT,
            output_config=bp.model_output_config(bp.obj({'lead':bp.STRING})),
            messages=[{'role':'user','content':json.dumps(writer_data,ensure_ascii=False)}])
        with qb.scope(job,'article'):response=measured(client,**request)
        lead=json.loads(ai.message_text(response))['lead'].strip()
        write(root/'proposal.json',{'lead':lead,'characters':len(lead)})
        need={'id':'d1','role':'primary','scope':'リードのみ','depends_on':[],'research_ids':['q1'],
            'question':'読者の望みやためらいから読む理由までが自然な一続きの導入になっているか',
            'answer_requirements':['冒頭で何を探す読者向けか自然に分かる','感情語の列挙でなく、何を満たしたくて何をためらうかが読者の立場で伝わる','文間の主語・視点・意味がつながり、記事を読むと得られる助けへ進む']}
        control_body='メールアドレスと迷惑メールフォルダを確認し、登録画面から一度再送してください。それでも届かなければ、サポートへ問い合わせます。'
        controls=[('a',title+'\n\n'+old_lead,need,'fail'),('b',title+'\n\n'+lead,need,'pass'),
            ('c','# 登録メールが届かないとき\n\n'+control_body,
                {**need,'question':'登録メールが届かないとき何をするか','answer_requirements':['確認・再送・未解決時の連絡先が分かる。感情描写や心理的なつなぎは不要。']},'pass')]
        if previous_trial:
            previous_lead=json.loads((previous_trial/'proposal.json').read_text())['lead']
            controls.append(('d',title+'\n\n'+previous_lead,need,'fail'))
        write(root/'control-plan.json',[{'id':i,'text_sha256':hashlib.sha256(t.encode()).hexdigest(),'expected':e} for i,t,n,e in controls])
        for ident,text,decision,expected in controls:
            request,article_design=capture(text,decision,'coverage',facts='新しい料金・統計・機能の断定は不要。',
                extra_requirements={'evaluation_scope':'提示した導入のみ。本文・比較表・商材紹介・まとめの省略を欠陥としない。',
                    'keyword':'登録メール 届かない' if ident=='c' else 'セカンドパートナー 探し方',
                    'custom_prompt':feedback if ident!='c' else '短い直接操作の回答。心理的な共感は求めていない。',
                    'measured_lead_chars':len(lead) if ident=='b' else len(old_lead) if ident=='a' else len(previous_lead) if ident=='d' else len(control_body)})
            with qb.scope(job,'quality'):value,usage=tr.checked_request(job,'coherence_control_'+ident,request)
            checks=fq.parse_focus(json.dumps({'checks':value['checks']}),fq.ROLES['coverage'],text)
            findings=ef.checked_findings(value,checks,text)
            checks=ad.checked_result(checks,value,article_design,text)
            actual='fail' if any(c['status']=='fail' for c in checks) else 'pass'
            result['controls'].append({'id':ident,'expected':expected,'actual':actual,'checks':checks,
                'lead_review':value.get('lead_review'),'editorial_findings':findings})
            write(root/'result.json',result);print(ident,actual,'expected',expected,flush=True)
        result.update(status='completed',lead_chars=len(lead),all_control_expectations_matched=all(c['expected']==c['actual'] for c in result['controls']),
            new_lead_scoped_qa_pass=result['controls'][1]['actual']=='pass',codex_editorial_acceptance=False,
            note='Model controls only. Codex reading and user review remain separate; no automatic promotion or article replacement.')
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:
        calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        cost=sum(c.get('cost_usd',c['reserved_usd']) for c in calls)
        result.update(new_usage_and_holds_usd=cost,prior_partial_usage_usd=prior_cost,
            combined_partial_usage_usd=prior_cost+cost,approved_partial_cap_usd=7.5,
            pending_calls=sum(c['status']=='pending' for c in calls),full_article_qa=False,production_changes=False)
        write(root/'result.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='controls'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
