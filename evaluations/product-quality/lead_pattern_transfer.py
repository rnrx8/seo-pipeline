"""Two synthetic writing-stage transfer controls under the same partial allowance.

Uses the real shared writer and coverage request; not fresh research/normal-flow QA.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from reader_depth_followup import BACKEND, sha, write
from stage6_replay import capture


def main():
    sys.stdout.reconfigure(line_buffering=True)
    prior,root=(Path(v).resolve() for v in sys.argv[1:3])
    previous=json.loads((prior/'result.json').read_text())
    manifest=json.loads((prior/'manifest.json').read_text())
    if previous.get('status')!='completed' or previous.get('pending_calls')!=0:
        raise RuntimeError('Previous lead trial is incomplete')
    histories=[];parent=prior
    while parent:
        if parent in histories:raise RuntimeError('Repeated parent history')
        histories.append(parent);record=json.loads((parent/'manifest.json').read_text())
        for value in record.get('extra_allowance_history',[]):
            history=Path(value)
            if history in histories:raise RuntimeError('Repeated extra history')
            histories.append(history)
        if record.get('prior_trial'):parent=Path(record['prior_trial'])
        elif record.get('previous_trial'):parent=Path(record['previous_trial'])
        elif 'prior_calls_sha256' in record:parent=Path(record['source'])
        else:parent=None
    past=[c for h in histories for p in (h/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    if any(c['status']=='pending' for c in past):raise RuntimeError('Prior request is pending')
    expense=sum(c.get('cost_usd',c['reserved_usd']) for c in past)
    if abs(expense-previous['combined_partial_usage_usd'])>1e-8:raise RuntimeError('Allowance history mismatch')
    cap=7.5-expense
    if cap<=0:raise RuntimeError('Allowance exhausted')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'started.json').open('x') as f:json.dump({'prior_usd':expense,'this_cap_usd':cap},f)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered',ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused',QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_TOTAL_LIMIT_USD=str(cap))
    sys.path.insert(0,str(BACKEND))
    from pipeline import db,ai,quality_budget as qb,tiered_research as tr
    from pipeline import step_article,editorial_blueprint as bp,focused_quality as fq
    from pipeline import article_design as ad,editorial_findings as ef
    def forbidden(*a,**kw):raise RuntimeError('Production DB forbidden')
    db._base=db._headers=forbidden
    db.get_optional_artifact=tr.get_optional_artifact=lambda *a:None
    tr.upsert_artifact=lambda **kw:kw
    qb.LIMIT=cap;qb.REVIEW_PHASE_LIMITS={'outline':cap,'article':cap}
    from dotenv import dotenv_values
    values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        if not values.get(name):raise RuntimeError('Existing provider credential unavailable')
        os.environ[name]=values[name]
    import anthropic
    client=anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],max_retries=0)
    send=ai.create_with_retry;counter=[0]
    def measured(*a,**kw):
        counter[0]+=1;name=f'{counter[0]:04d}.json';write(root/'requests'/name,kw)
        response=send(*a,**kw)
        write(root/'responses'/name,{'model':response.model,'text':ai.message_text(response),
            'usage':response.usage.model_dump() if hasattr(response.usage,'model_dump') else vars(response.usage)})
        return response
    ai.create_with_retry=tr.create_with_retry=measured
    cases=[
        {'id':'direct','title':'登録メールが届かないときの確認と再送','keyword':'登録メール 届かない',
         'request':'短い操作案内。必要な確認・再送・未解決時の行動をすぐ伝える。心理描写や共感を求めていない。',
         'editorial_direction':'直接の答えから、実行の順序が分かる入口にする。',
         'facts':'架空サービスの試験条件：入力メールアドレスと迷惑メールフォルダを確認でき、登録画面に再送ボタンがある。届かない場合はサポートへ問い合わせられる。待機時間や機能を追加しない。',
         'reader_context':'登録確認メールが届かず、次の操作を知りたい。',
         'answer_requirements':['必要な操作がすぐ分かり、不要な心理説明を前置きしない','提供された架空条件内で確認・再送・未解決時の対応を案内する']},
        {'id':'learning','title':'学び直しの方法を比較して自分に合う学び方を選ぶ','keyword':'学び直し 方法',
         'request':'180字前後の比較記事の導入。自分でも学び進められる感覚を得たいという気持ちを、具体的な事情から扱う。検索語を疑問に置き換えた問いかけや、読者全員の内面の断定は避ける。先に講座を勧めず、本文で本・動画・講座を比べる意味へつなぐ。',
         'editorial_direction':'得たい変化と、分からない所で一人で止まりやすい事情を素材に、方法を選ぶ意味が伝わる入口にする。',
         'facts':'架空の比較条件：本は自分のペースで読めるが質問はできない。動画は見返せるが、この試験の動画に質問対応はない。講座は質問対応があるが、受講時間が決まっている。料金・効果・成功率・利用者の実績データは与えていない。',
         'reader_context':'仕事後に学び直したいが、小さな疑問を一人で抱えると先へ進めなくなるという仮説。目標は疑問を解きながら自分でも学べる感覚を得ること。全読者の経験ではない。',
         'answer_requirements':['学び直しの方法を選ぶ主題が冒頭から分かる','望む変化や具体的な事情から、方法を比べる意味へ自然につながる','本文で比較する前に講座へ推薦を決めず、数値・経験・効果を創作しない']}
    ]
    write(root/'manifest.json',{'scope':'Synthetic writing-stage transfer, not normal-flow reproducibility',
        'prior_trial':str(prior),'prior_calls_sha256':sha(past),'prior_usage_usd':expense,'cap_usd':cap,
        'cases':cases,'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (BACKEND/'pipeline').glob('*.py')},
        'handwritten_lead_examples_in_generation':False,'production_changes':False})
    result={'status':'running','cases':[]}
    try:
        for case in cases:
            request=dict(model=step_article.MODEL,max_tokens=1000,system=step_article.SYSTEM_PROMPT,
                output_config=bp.model_output_config(bp.obj({'lead':bp.STRING})),
                messages=[{'role':'user','content':json.dumps({'scope':'与えられた試験条件から記事のリードだけを1案JSONのleadへ返す。',**case},ensure_ascii=False)}])
            with qb.scope('lead-pattern-transfer','article'):response=measured(client,**request)
            lead=json.loads(ai.message_text(response))['lead'].strip();text='# '+case['title']+'\n\n'+lead
            need={'id':'d1','role':'primary','scope':'導入のみ','depends_on':[],'research_ids':['q1'],
                  'question':case['keyword'],'answer_requirements':case['answer_requirements']}
            req,design=capture(text,need,'coverage',facts=case['facts'],extra_requirements={
                'keyword':case['keyword'],'custom_prompt':case['request'],
                'evaluation_scope':'生成された導入だけを確認。架空条件に基づく対照試験。未提示の本文やサービスを要求しない。'})
            with qb.scope('lead-pattern-transfer','quality'):value,usage=tr.checked_request('lead-pattern-transfer',case['id'],req)
            checks=fq.parse_focus(json.dumps({'checks':value['checks']}),fq.ROLES['coverage'],text)
            findings=ef.checked_findings(value,checks,text);checks=ad.checked_result(checks,value,design,text)
            result['cases'].append({'id':case['id'],'lead':lead,'lead_chars':len(lead),
                'valid':all(c['status']!='fail' for c in checks),'checks':checks,
                'lead_review':value.get('lead_review'),'editorial_findings':findings})
            write(root/'result.json',result);print(case['id'],result['cases'][-1]['valid'],flush=True)
        result.update(status='completed',user_review='pending',full_article_qa=False,codex_acceptance=False)
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        cost=sum(c.get('cost_usd',c['reserved_usd']) for c in calls)
        result.update(new_usage_usd=cost,prior_usage_usd=expense,combined_partial_usage_usd=expense+cost,
            pending_calls=sum(c['status']=='pending' for c in calls),approved_partial_cap_usd=7.5,production_changes=False)
        write(root/'result.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
