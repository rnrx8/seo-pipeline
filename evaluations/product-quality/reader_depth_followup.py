"""Scoped analysis/writing/coverage proof within the prior trial's remainder.

No article overwrite, source collection, production DB, deployment or full-run pass.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

BACKEND=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BACKEND))
from stage6_replay import capture


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    sys.stdout.reconfigure(line_buffering=True)
    root=Path(sys.argv[1]).resolve();source=Path(sys.argv[2]).resolve();live='--live' in sys.argv
    if (root/'started.json').exists():raise RuntimeError('Followup already started; inspect saved responses, do not duplicate')
    calls=[c for p in (source/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    if any(c['status']=='pending' for c in calls):raise RuntimeError('Prior request still pending')
    prior_cost=sum(c.get('cost_usd',c['reserved_usd']) for c in calls);cap=17.5-prior_cost
    if cap<=0:raise RuntimeError('No previously approved budget remaining')
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered',ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused',QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root/'ledger'),QUALITY_TOTAL_LIMIT_USD=str(cap))
    from pipeline import db,ai,quality_budget as qb,step_intent,step_article,reader_needs as rn
    from pipeline import focused_quality as fq,editorial_findings as ef
    from pipeline.tiered_research import checked_request
    def forbidden(*a,**kw):raise RuntimeError('External DB/network side effects forbidden')
    db._base=db._headers=forbidden;db.get_optional_artifact=lambda *a:None
    qb.LIMIT=cap;qb.REVIEW_PHASE_LIMITS={'outline':cap,'article':cap}
    root.mkdir(parents=True,exist_ok=True)
    def artifact(name):return json.loads((source/'artifacts'/(name+'.json')).read_text())
    original=artifact('article')['content_text'];old_lead=original.split('\n\n')[1]
    original_request=json.loads((source/'requests/0002.json').read_text())
    data=json.loads(original_request['messages'][0]['content']);brief=copy.deepcopy(data['production_brief'])
    feedback='リードは元の275字の約3分の2、目安180字。主題と読む動機を保ち、求める関係やためらいにも短く触れる。比較・商材紹介は、何を得たい・守りたいから不安なのかを一段深く理解し、特徴の作用と具体策へつなぐ。長い心理解説ではなく説明を改善する。末尾に結論と次の一歩を回収する短いまとめを置く。カテゴリと既婚者クラブの推薦、既存の章の役割、事実条件は維持する。'
    brief['request']['custom_prompt']='\n'.join(filter(None,[brief['request'].get('custom_prompt'),feedback]))
    source_hashes={n:hashlib.sha256((source/'artifacts'/(n+'.json')).read_bytes()).hexdigest()
        for n in ('article','outline','production_brief','reader_needs','product_context')}
    manifest={'scope':'New analysis and four excerpt proposals with existing shared writer; blinded coverage controls only',
        'prior_trial':str(source),'prior_calls_sha256':sha(calls),'prior_cost_with_holds_usd':prior_cost,
        'combined_cap_usd':17.5,'this_followup_cap_usd':cap,'source_hashes':source_hashes,
        'feedback':feedback,'lead_target_chars':180,'new_sources':False,'source_facts_frozen_at':'2026-10-18',
        'full_article_regeneration':False,'production_changes':False,
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
        'preregistered_expectations':{'a1':'fail','a2':'pass','b1':'fail','b2':'pass','c1':'fail','c2':'pass','d1':'pass'}}
    write(root/'manifest.json',manifest)
    if not live:print(json.dumps(manifest,ensure_ascii=False));return
    with (root/'started.json').open('x') as f:json.dump({'source_hashes':source_hashes},f)
    from dotenv import dotenv_values
    values=dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        if not values.get(key):raise RuntimeError('Existing provider credential unavailable')
        os.environ[key]=values[key]
    import anthropic
    client=anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],max_retries=0)
    normal=ai.create_with_retry;counter=[0]
    def measured(*args,**kwargs):
        counter[0]+=1;ident=f'{counter[0]:04d}'
        write(root/'requests'/(ident+'.json'),kwargs)
        response=normal(*args,**kwargs)
        write(root/'responses'/(ident+'.json'),{'text':ai.message_text(response),'model':response.model,
            'usage':response.usage.model_dump() if hasattr(response.usage,'model_dump') else vars(response.usage)})
        return response
    from pipeline import tiered_research
    ai.create_with_retry=step_intent.create_with_retry=tiered_research.create_with_retry=measured
    saved={};result={'status':'running','cases':[]}
    budget_job='reader-depth-followup'
    try:
        # Actual normal reader-analysis request/validation; reuse estimated query
        # hints and supplied product context, never the old reader hypotheses.
        with (qb.scope(budget_job,'search_intent'),patch.object(step_intent,'prepare',return_value=(brief,data['product_materials'],json.loads(artifact('product_context')['content_text']),'supplied_context_reused')),
             patch.object(step_intent,'get_artifact',side_effect=lambda job,name:artifact(name)),
             patch.object(step_intent,'_generate_query_attrs',return_value=data.get('query_hints',{})),
             patch.object(step_intent,'upsert_artifact',side_effect=lambda **kw:saved.update({kw['step']:kw}) or kw)):
            step_intent.run(budget_job,brief['keyword'],os.environ['ANTHROPIC_API_KEY'])
        for name,value in saved.items():write(root/'analysis_artifacts'/(name+'.json'),value)
        needs=json.loads(saved['reader_needs']['content_text'])
        print('New normal reader analysis validated',flush=True)
        facts={'category':'既婚者専用サービスは既婚者を登録対象とする。本人・年齢確認と婚姻状況の確認は区別する。',
            'service':'既婚者クラブ。18歳以上の既婚者が対象。写真ぼかし、ニックネーム、Webブラウザ利用、条件検索。',
            'free':'男女とも登録・プロフィール登録・相手検索は無料。男性メッセージは有料。',
            'limits':'顔写真ぼかしでもプロフィールや共有端末から知られうる。相手との関係や出会いの成果は保証しない。'}
        schema=rn.obj({k:rn.STR for k in ('lead','method_detail','product_detail','closing_title','closing')})
        writer_model=artifact('article_part_1_attempt_1').get('meta',{}).get('model','claude-sonnet-5-5')
        request=dict(model=writer_model,max_tokens=4096,system=step_article.SYSTEM_PROMPT,
            messages=[{'role':'user','content':json.dumps({'scope':'全文ではなく、リード、方法H3の説明、商品H3の説明、末尾まとめの4抜粋だけを生成。H2直下の結論・短い理由・表という配置は維持し、深い説明は該当H3へ置く。表や既存本文の再出力は不要。',
                'request':brief['request'],'reader_needs':needs['selected'],'confirmed_facts':facts,
                'lead_target_chars':180,'lead_max_chars':200,'chapter_roles':{'method_detail':'方法を選ぶ理由と、背景の望み・懸念に応える具体策を示す。','product_detail':'既婚者クラブの推薦と、特徴が望みに役立つ作用・無理のない始め方を示す。','closing':'新情報を足さず、結論・理由・次の一歩を短く回収する。'}},ensure_ascii=False)}],
            output_config={'format':{'type':'json_schema','schema':schema}})
        with qb.scope(budget_job,'article'):response=measured(client,**request)
        proposal=json.loads(ai.message_text(response));write(root/'proposal.json',proposal)
        old_method=re.search(r'### 既婚者専用マッチングで探す\n([\s\S]*?)(?=\n### )',original)[1]
        old_product=re.search(r'## セカンドパートナー探しには既婚者クラブがおすすめ\n([\s\S]*?)(?=\n### )',original)[1]
        depth_need={'id':'d1','role':'primary','scope':'抜粋の読者価値','depends_on':[],'research_ids':['q1'],
            'question':'気持ちを話せる相手を求める一方、大切な生活や自分のペースを守りたい読者に、勧める理由と具体策が伝わるか',
            'answer_requirements':['何を得たい・守りたいからためらうかと、特徴の作用を結び付ける','望む関係や目的を確かめるなど、読者が不安に対処する具体策を示す','指定カテゴリと商材を勧める方向・事実条件を維持する']}
        lead_need={**depth_need,'question':'短い導入で対象と望み・ためらい、読む意味が伝わるか','answer_requirements':['主題と対象を冒頭で示す','約180字、200字以内で不要な定義や列挙を省く','求めるものとためらいが、方法を選ぶ意味へつながる']}
        closing_need={**depth_need,'question':'記事末尾で選ぶ結論と理由、読者の次の一歩を回収するか','answer_requirements':['既婚者専用マッチングと既婚者クラブを勧める結論を回収する','読者の望みにつながる理由と、まずできる一歩を示す','新しい事実や別の不安を持ち込まない']}
        direct={**depth_need,'question':'登録メールが届かないときの操作が分かるか','answer_requirements':['確認する場所と再送手順','未解決時の問い合わせ先']}
        cases=[('a1','# セカンドパートナーの探し方\n\n'+old_lead,lead_need,'fail','リードだけを検査。本文・まとめの省略は欠陥ではない。'),
            ('a2','# セカンドパートナーの探し方\n\n'+proposal['lead'],lead_need,'pass','リードだけを検査。本文・まとめの省略は欠陥ではない。'),
            ('b1','## 方法を選ぶ理由\n'+old_method+'\n## 既婚者クラブの推薦\n'+old_product,depth_need,'fail','方法・商品説明の抜粋だけ。料金表や別章の省略は欠陥ではない。'),
            ('b2','## 方法を選ぶ理由\n\n'+proposal['method_detail']+'\n\n## 既婚者クラブの推薦\n\n'+proposal['product_detail'],depth_need,'pass','方法・商品説明の抜粋だけ。料金表や別章の省略は欠陥ではない。'),
            ('c1','## 関係の目的とルール\n\n'+original.rsplit('\n\n',1)[-1],closing_need,'fail','複数章の比較記事の結末だけを検査。主回答は既婚者専用マッチング、商材は既婚者クラブ。章本文の省略ではなく、記事末尾が判断を回収しているかを見る。'),
            ('c2','## '+proposal['closing_title']+'\n\n'+proposal['closing'],closing_need,'pass','複数章の比較記事の結末だけを検査。前の章で主回答・料金・機能を説明済み。'),
            ('d1','# 登録メールが届かないとき\n\n入力したメールアドレスと迷惑メールフォルダを確認し、登録画面から一度再送してください。届かないときはサポートへ問い合わせます。',direct,'pass','短い直接操作の回答。心理の掘り下げ・独立したまとめ・商材推薦は不要。')]
        write(root/'case-plan.json',[{'id':i,'text_sha256':hashlib.sha256(t.encode()).hexdigest(),'expected':e,'scope':s} for i,t,n,e,s in cases])
        for ident,text,need,expected,scope in cases:
            request,design=capture(text,need,'coverage',facts=json.dumps(facts,ensure_ascii=False),extra_requirements={'evaluation_scope':scope,'custom_prompt':feedback,'article_purpose':'cv' if ident.startswith(('b','c')) else '情報提供'})
            with qb.scope(budget_job,'quality'):
                value,usage=checked_request(budget_job,'depth_followup_'+ident,request)
            checks=fq.parse_focus(json.dumps({'checks':value['checks']}),fq.ROLES['coverage'],text)
            findings=ef.validate(value,fq.ROLES['coverage'],text)
            from pipeline import article_design as ad
            ad.checked_result(checks,value,design,text)
            actual='fail' if any(c['status']=='fail' for c in checks) else 'pass'
            result['cases'].append({'id':ident,'expected':expected,'actual':actual,'expected_matched':actual==expected,'checks':checks,'editorial_findings':findings,'lead_review':value.get('lead_review')})
            write(root/'result.json',result);print(ident,actual,'expected',expected,flush=True)
        result.update(status='completed',lead_chars=len(proposal['lead']),lead_target_met=len(proposal['lead'])<=200)
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        new_calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        cost=sum(c.get('cost_usd',c['reserved_usd']) for c in new_calls)
        result.update(new_cost_with_holds_usd=cost,prior_cost_with_holds_usd=prior_cost,combined_cost_with_holds_usd=prior_cost+cost,
            combined_cap_usd=17.5,pending_calls=sum(c['status']=='pending' for c in new_calls),
            all_expectations_matched=len(result['cases'])==7 and all(c['expected_matched'] for c in result['cases']),
            full_article_regenerated=False,source_artifacts_unchanged=all(hashlib.sha256((source/'artifacts'/(n+'.json')).read_bytes()).hexdigest()==v for n,v in source_hashes.items()))
        write(root/'result.json',result)


if __name__=='__main__':main()
