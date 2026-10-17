"""Use exact current role requests on scoped controls; never a whole-run pass.

Model requests and responses are local evidence. DB access is replaced before
importing stages. Only existing provider keys are read, no secret is persisted.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def controls():
    method = {'id':'d1','question':'どの学習方法が自分に合うか','role':'primary','scope':'方法選択','depends_on':[], 'research_ids':['q1'],
        'answer_requirements':['本・動画・講座それぞれの特徴と向く状況を説明する','主な違い・制約から自分に合う方法を選べる']}
    values = [
        ('method-list', '学習方法には本、動画、講座があります。おすすめサービスAに登録しましょう。', method, 'fail'),
        ('method-choice', '費用を抑え、自分のペースで進めたいなら本が候補です。苦手な箇所へ戻れますが、質問への回答は自分で探す必要があります。\n\n動きを見ながら理解したいなら動画が候補です。停止や再生を使って学べますが、視聴だけで終わらないよう、学んだ操作を試しましょう。\n\n質問して疑問を解消しながら学びたいなら講座が候補です。質問対応の有無と受講時間・料金を確認し、予算や生活時間に合うものを選びます。', method, 'pass'),
        ('single-operation', 'まず入力したメールアドレスと迷惑メールフォルダを確認します。間違いがなければ登録画面の再送ボタンを1回押してください。届かない場合は、登録メールアドレスを添えてサポートへ問い合わせます。',
         {**method,'question':'登録確認メールが届かないとき何をするか','answer_requirements':['確認する場所と再送の順序が分かる','解決しない場合の問い合わせ方法が分かる']}, 'pass'),
        ('direct-comparison', 'Aは低料金で条件を自分で選びたい人、Bは候補の提案から選びたい人に合います。Aの月額は2,000円、Bは3,000円で、両方とも1か月契約の税込価格です。まず無料範囲で操作を比べ、使いやすい方を選びましょう。',
         {**method,'question':'サービスAとBのどちらがよいか','answer_requirements':['料金と使い方の違いが分かる','希望に応じて選び分けられる']}, 'pass'),
        ('latent-need-marketing', '写真を投稿できれば十分だと思っていても、実際に撮り始めると「手元の動きを見せたい」「文章で補足したい」と気づくことがあります。\n\n写真中心で手軽に記録したいならA、動きと説明を一緒に残したいならBが候補です。まずはAの無料範囲で写真を投稿し、動画の説明が必要になったらBの無料体験で操作を比べると、自分に必要な機能を判断できます。\n\n次の記録を作る際に、Aの投稿画面を試してみましょう。',
         {**method,'question':'記録サービスAとBをどう選ぶか','answer_requirements':['写真中心と動画・補足説明の用途を選び分けられる','自分に必要な機能を確かめる行動が分かる']}, 'pass'),
        ('missing-required-condition', '機能Xが使えます。通常プランは月額1,000円です。まず通常プランを選びましょう。',
         {**method,'question':'自分のプランで機能Xが使えるか','answer_requirements':['機能Xは上位プランだけで使えると分かる','必要な上位プランの月額3,000円から選べる']}, 'fail'),
    ]
    return values


def capture(text, decision, selected):
    from pipeline import content_quality as cq, focused_quality as fq
    design = {'version':'article-reader-design-v1','blueprint_sha256':'scoped-evaluation-only',
        'reader_decisions':[decision], 'sections':[]}
    requests = {}
    def fake(job, step, request, **kwargs):
        role='evidence' if step=='tiered_article_evidence_scope' else step.removeprefix('tiered_article_')
        requests[role]=request
        value={'checks':[{'key':key,'status':'pass','reason':'request capture only','affected_blocks':[],
            **({'requires_source_check':False,'research_ids':[],'source_urls':[]} if role in ('evidence','coverage') else {})} for key in fq.ROLES[role]]}
        if role!='evidence':value['editorial_findings']=[]
        if role=='coverage':value['decision_reviews']={decision['id']:{'status':'pass','reason':'request capture only','missing_requirements':[],'affected_blocks':[]}}
        return value, NS(input_tokens=0,output_tokens=0)
    facts='[confirmed] 試験用の架空サービス：Aは写真中心、Bは動画と補足説明に対応。機能Xは上位プラン月額3,000円だけで利用でき、通常プラン1,000円では利用できない。'
    if decision['question']=='サービスAとBのどちらがよいか':
        facts='[confirmed] 架空条件：Aは条件を自分で選ぶ使い方、Bは候補の提案から選ぶ使い方。Aは税込月額2,000円、Bは税込月額3,000円、いずれも1か月契約。両者の無料範囲で操作を試せる。'
    with patch('pipeline.tiered_research.checked_request',side_effect=fake):
        fq.audit_article(None,text=text,facts=facts,outline='## 回答',contract={},
            requirements={'keyword':decision['question'],'article_design':design,
                'evaluation_scope':'この抜粋内の問いへの充足だけを検査。架空条件を与えた対照試験で、実在サービスや全記事の合格ではない。'},sources='[]')
    return requests[selected], design


def main():
    root=Path(sys.argv[1]).resolve()
    live='--live' in sys.argv
    if live:
        from dotenv import dotenv_values
        values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
        for key in ('OPENAI_API_KEY',):
            if not values.get(key):raise RuntimeError('Existing provider credential unavailable')
            os.environ[key]=values[key]
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered',ARTICLE_WRITING_FLOW='sonnet_opus',QUALITY_RESEARCH_ROUTING='focused',
        QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_BUDGET_MODE='bounded',QUALITY_TOTAL_LIMIT_USD='2.5')
    from pipeline import db
    artifacts={}
    def forbidden(*args,**kwargs):raise AssertionError('Production DB access forbidden')
    db._base=db._headers=forbidden
    db.get_optional_artifact=lambda job, step:copy.deepcopy(artifacts.get((job,step)))
    def save(**kw):
        artifacts[kw['job_id'],kw['step']]={'id':'local',**kw}
        return copy.deepcopy(artifacts[kw['job_id'],kw['step']])
    db.upsert_artifact=save
    from pipeline import quality_budget as qb, content_quality as cq, focused_quality as fq, article_design as ad, editorial_findings as ef
    from pipeline.tiered_research import checked_request
    qb.LIMIT=2.5
    qb.REVIEW_PHASE_LIMITS={'outline':2.5,'article':2.5}
    # Every scoped case has one selected role, not five paid reviewers. Combined
    # spend is enforced here before requests using the adapter's conservative bound.
    result={'status':'prepared','scope':'selected existing QA role on controlled excerpts; no full article generation or acceptance','cases':[], 'cap_usd':2.5,'jpy_per_usd_for_planning':200}
    requests=[]
    selected_cases=sys.argv[sys.argv.index('--case')+1].split(',') if '--case' in sys.argv else None
    for ident,excerpt,decision,expected in controls():
        if selected_cases and ident not in selected_cases:continue
        if ident=='latent-need-marketing':
            excerpt=excerpt.replace('写真中心で手軽に記録したいならA、動きと説明を一緒に残したいならBが候補です。','')
            excerpt='写真中心で手軽に記録したいならA、動きと説明を一緒に残したいならBが候補です。\n\n'+excerpt
        text='## '+decision['question']+'\n\n'+excerpt
        for role in (['coverage','organization'] if ident=='latent-need-marketing' else ['coverage']):
            name=ident+'-'+role
            request,design=capture(text,decision,role)
            requests.append((name,request,design,text,expected,role))
    result['status']='running' if live else 'prepared'
    write(root/'result.json',result)
    for name,request,design,text,expected,role in requests:
        folder=root/name
        write(folder/'request.json',request)
        row={'id':name,'role':role,'expected':expected,'text_sha256':cq.digest(text),'request_sha256':cq.digest(json.dumps(request,ensure_ascii=False,sort_keys=True))}
        if not live:
            result['cases'].append(row);continue
        # A single-use marker prevents a new process from paying twice.
        folder.mkdir(parents=True,exist_ok=True)
        with (folder/'started.json').open('x') as f:json.dump({'pid':os.getpid()},f)
        used=sum(c.get('cost_usd',c['reserved_usd']) for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text()).get('calls',[]))
        upper=(len(json.dumps(request,ensure_ascii=False).encode())+10000)*2.5/1e6+request['max_tokens']*10/1e6
        if used+upper>2.5:raise RuntimeError('Replay aggregate ceiling would be exceeded before request')
        job='stage6-'+name
        try:
            with qb.scope(job,'content_audit'):
                value,usage=checked_request(job,'tiered_article_'+role,request)
            write(folder/'response.json',value)
            checks=fq.parse_focus(json.dumps(value,ensure_ascii=False),fq.ROLES[role],text)
            details=ef.checked_findings(value,checks,text)
            if role=='coverage':
                checks=ad.checked_result(checks,value,design,text)
                details+=ef.decision_findings(value,design)
            actual='fail' if any(c['status']=='fail' for c in checks) else 'pass'
            row.update(actual=actual,expected_matched=actual==expected,checks=checks,editorial_findings=details,input_tokens=usage.input_tokens,output_tokens=usage.output_tokens)
        except Exception as exc:
            row.update(status='error',error_type=type(exc).__name__,error=str(exc))
            result['cases'].append(row);write(root/'result.json',result);raise
        result['cases'].append(row);write(root/'result.json',result)
        print(name,actual,'expected',expected,flush=True)
    result['status']='completed' if live else 'prepared'
    if live:
        calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text()).get('calls',[])]
        result.update(paid_calls=len(calls),cost_usd=sum(c.get('cost_usd',c['reserved_usd']) for c in calls),unsettled_calls=sum(c['status']!='accounted' for c in calls),all_expected_matched=all(r.get('expected_matched') for r in result['cases']))
    write(root/'result.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
