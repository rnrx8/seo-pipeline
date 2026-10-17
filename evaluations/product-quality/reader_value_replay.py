"""Existing coverage QA on the rejected article and blinded excerpt controls.

The old two QA ledgers and this run share the previously declared $2.5 cap.
This does not generate articles, revise old verdicts or access the production DB.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

BACKEND=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BACKEND))
from stage6_replay import capture


def sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def controls():
    base={'id':'d1','role':'primary','scope':'指定範囲の読者判断','depends_on':[],'research_ids':['q1']}
    lead={**base,'question':'英語の学習方法を探す読者に、何のテーマで誰向けかと読む理由が伝わるか',
          'answer_requirements':['冒頭の短い範囲で主題と対象読者が分かる','使った背景から方法選択への関係と読後の判断がつながる']}
    facts='[confirmed] 架空の対照条件：本は自分のペースで読み返せる。動画は操作を停止・再生して見られる。講座は講師に質問でき、開催時間に参加する。'
    lead_scope={'evaluation_scope':'リード抜粋だけを評価。本文の方法詳細・表・商材紹介の省略は欠陥にしない。導入の主題・対象と、背景から読む理由・得る判断へのつながりを確認する。','article_purpose':'情報提供'}
    cases=[
        ('lead-unanchored','# 英語学習の始め方\n\nもっと自由に話せるようになりたい。でも、また途中でやめてしまうのは避けたい。そんな方に向けて、この記事では英語の本・動画・講座の特徴を紹介します。',lead,'fail',facts,lead_scope),
        ('lead-no-bridge','# 英語学習の始め方\n\n英語の学習方法を探している方には、続けられるか、分からない所で止まらないかが気になる人もいるでしょう。この記事では、本・動画・講座の特徴を紹介します。',lead,'fail',facts,lead_scope),
        ('lead-connected','# 英語学習の始め方\n\n英語の学習方法を探している方には、続けられるか、分からない所で止まらないかが気になる人もいるでしょう。本・動画・講座では、自分で進める範囲や質問できる場面が違います。この記事では、使える時間と質問したい場面を基準に、自分の続け方に合う方法を選べるよう違いを整理します。',lead,'pass',facts,lead_scope),
    ]
    procedure={**base,'question':'登録メールが届かない読者に確認と解決の見通しが伝わるか',
               'answer_requirements':['対象の問題が冒頭で分かる','確認・再送・解決しない場合の問い合わせが分かる']}
    cases.append(('direct-howto-no-empathy','# 登録メールが届かないとき\n\n登録メールが届かないときは、入力したメールアドレスと迷惑メールフォルダを確認し、登録画面から一度再送してください。この記事では、確認と再送の順序、解決しないときの問い合わせ方法を案内します。',procedure,'pass','[confirmed] 架空条件：登録画面に再送機能があり、未解決ならサポートへ問い合わせられる。',lead_scope))
    selection={**base,'question':'英語をどう学ぶ方法を選ぶか','answer_requirements':['方法の特徴が分かる','読者の条件なら何を優先して選ぶかと理由が分かる']}
    neutral='本は自分のペースで読み返せます。動画は操作を停止・再生しながら見られます。講座では講師に質問できますが、開催時間への参加が必要です。自分の生活に合わせて選びましょう。'
    directed='分からない所を講師に質問して進めたい人には、質問できる講座をおすすめします。開催時間に参加できることが条件です。決まった時間を取りにくく自分で読み進めたいなら本、操作を止めて見直しながら試したいなら動画が候補になります。'
    scope={'evaluation_scope':'方法選択の本文抜粋だけを評価。個別商品の紹介は後続章で行うため、この抜粋への商品名・CTAの強制はしない。','article_purpose':'cv','service_id':'synthetic-A'}
    cases.extend([('descriptive-methods','# 英語の学習方法\n\n## 自分に合う方法の選び方\n\n'+neutral,selection,'fail',facts,scope),('conditional-method-recommendation','# 英語の学習方法\n\n## 自分に合う方法の選び方\n\n'+directed,selection,'pass',facts,scope)])
    product={**base,'question':'質問しながら学びたい人に講座Aを勧める理由は何か','answer_requirements':['対象読者と確認済みの特徴が結び付く','条件が合う場合にこの商品を勧める結論が分かる']}
    product_facts='[confirmed] 架空条件：講座Aは自習と予約したオンライン質問時間を組み合わせる。質問時間への参加が必要。無料で教材の操作を試せる。競合より優れるという調査はない。'
    product_scope={'evaluation_scope':'個別商品の推薦段落だけを評価。料金表や全記事は別に提示済みという対照条件。','article_purpose':'cv','service_id':'synthetic-A'}
    cases.extend([('product-only-candidate','# 講座の検討\n\n## 講座Aの紹介\n\n講座Aは、自習と予約したオンライン質問時間を組み合わせるサービスです。質問時間への参加が必要です。質問しながら学びたい人の選択肢の一つになります。無料で教材の操作を試せます。',product,'fail',product_facts,product_scope),('product-clear-fit-recommendation','# 講座の検討\n\n## 講座Aを勧める理由\n\n自習中の疑問をまとめて講師に質問したい人には、講座Aをおすすめします。自習と予約したオンライン質問時間を組み合わせられるためです。質問時間に参加できる人は、まず無料で教材の操作を試すと、学び方が合うかを確かめられます。',product,'pass',product_facts,product_scope)])
    return cases


def objective_controls():
    decision={'id':'d1','role':'primary','scope':'学習方法比較の抜粋','depends_on':[],'research_ids':['q1'],
        'question':'英語の学習方法をどう選ぶか','answer_requirements':['本・動画・オンライン講座の特徴と選ぶ理由を説明する']}
    facts='[confirmed] 架空条件：本は自分の時間で読み返せる。動画は停止・再生しながら復習できる。オンライン講座は講師へ質問して学べ、予約時間への参加が必要。講座Aはオンライン講座で自習と予約質問を組み合わせる。競合への優越や効果率は未確認。'
    scope={'article_purpose':'cv','featured_service':'講座A','request':{'article_purpose':'講座Aへの申込みを促す','featured_service':'講座A'},
        'reader_design':{'request':{'article_purpose':'講座Aへの申込みを促す'},'reader_needs':[{'tone':'中立に比較し、推奨を押し付けない'}]},
        'evaluation_scope':'記事の方法比較H2だけを評価。個別商品の紹介・料金・CTAは後続H2で示すため、この抜粋への追加は不要。入力の元の記事目的と実際の比較結論を確認する。'}
    common='本は好きな時間に読み返せ、動画は止めながら復習できます。オンライン講座では講師へ質問できますが、予約時間への参加が必要です。'
    absent='自分の時間で進めたい方には本をおすすめします。繰り返し見たい方には動画、質問したい方にはオンライン講座も候補になります。'
    directed='英語を学び始めるなら、疑問を講師に質問して進められるオンライン講座をまずおすすめします。分からない所を確認しながら学べるためです。予約時間に参加できるかを確かめて選びましょう。自分の時間だけで進めたい方には本、繰り返し見て復習したい方には動画が向いています。'
    return [('objective-lost-despite-conditional-choice','# 英語学習の始め方\n\n## 学習方法の選び方\n\n'+absent+'\n\n'+common,decision,'fail',facts,scope),
            ('objective-led-category-recommendation','# 英語学習の始め方\n\n## 学習方法の選び方\n\n'+directed+'\n\n'+common,decision,'pass',facts,scope)]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);parser.add_argument('--source-trial',type=Path,required=True);parser.add_argument('--live',action='store_true');parser.add_argument('--lead-followup',action='store_true');parser.add_argument('--objective-followup',action='store_true');args=parser.parse_args()
    if args.lead_followup and args.objective_followup:parser.error('Choose one followup mode')
    root=args.root.resolve();source=args.source_trial.resolve();prior_roots=[source.parent/'qa-replay',source.parent/'qa-replay-corrected']
    if args.lead_followup or args.objective_followup:prior_roots.append(source.parent/'reader-value-revision')
    if args.objective_followup:prior_roots.append(source.parent/'reader-value-revision-v2')
    def calls(folder):return [c for p in (folder/'ledger').glob('*.json') for c in json.loads(p.read_text()).get('calls',[])]
    prior=[c for folder in prior_roots for c in calls(folder)]
    if len(prior)!=(22 if args.objective_followup else 19 if args.lead_followup else 10) or any(c['status']!='accounted' for c in prior):raise RuntimeError('Prior QA expenses differ from the completed calls')
    prior_cost=sum(c['cost_usd'] for c in prior);cap=2.5
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered',QUALITY_RESEARCH_ROUTING='focused',ARTICLE_WRITING_FLOW='sonnet_opus',QUALITY_BUDGET_MODE='bounded',QUALITY_BUDGET_DIR=str(root/'ledger'))
    from pipeline import db
    def forbidden(*a,**kw):raise RuntimeError('Production DB access forbidden')
    db._base=db._headers=forbidden;db.get_optional_artifact=lambda *a:None;db.upsert_artifact=lambda **kw:kw
    from pipeline import article_design as ad,quality_budget as qb,focused_quality as fq,editorial_findings as ef
    from pipeline.tiered_research import checked_request
    candidates=[]
    # Reuse the unchanged final article and fact packet, with the new common rubric.
    previous=[]
    for p in sorted((source/'requests').glob('*.json')):
        request=json.loads(p.read_text());properties=request.get('output_config',{}).get('format',{}).get('schema',{}).get('properties',{})
        if 'decision_reviews' in properties and 'editorial_findings' in properties:previous.append(request)
    frozen=previous[-1];payload=json.loads(frozen['messages'][0]['content']);req=copy.deepcopy(payload['requirements'])
    outline_artifact=json.loads((source/'artifacts/outline.json').read_text());article=json.loads((source/'artifacts/article.json').read_text())['content_text']
    design=ad.design_context(outline_artifact)
    req['evaluation_scope']='完成記事のcoverageを現在の共通基準で確認する。旧判定は提示しない。取得済みの根拠と省略状態を保ち、本文の実際の説明と章の約束を検査する。'
    request,design=capture(article,design['reader_decisions'][0],'coverage',facts=payload['confirmed_facts'],extra_requirements=req,design=design,outline=payload['outline'])
    if not args.objective_followup:candidates.append(('unchanged-rejected-article',request,design,article,'fail'))
    for ident,text,decision,expected,facts,req in (objective_controls() if args.objective_followup else controls()):
        if args.lead_followup and ident not in ('lead-connected','direct-howto-no-empathy'):continue
        request,design=capture(text,decision,'coverage',facts=facts,extra_requirements=req)
        candidates.append((ident,request,design,text,expected))
    manifest={'scope':('Existing coverage role on objective-led category recommendation controls; no article regeneration or whole-flow acceptance' if args.objective_followup else 'Existing coverage role only; unchanged rejected article plus blinded controls; no regeneration or whole-flow acceptance'),
              'combined_qa_cap_usd':cap,'prior_qa_cost_usd':prior_cost,'new_run_available_usd':cap-prior_cost,
              'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
              'prior_ledger_sha256':[sha(calls(folder)) for folder in prior_roots],
              'cases':[{'id':name,'expected':expected,'request_sha256':sha(request),'text_sha256':hashlib.sha256(text.encode()).hexdigest()} for name,request,_,text,expected in candidates]}
    path=root/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:raise RuntimeError('Prepared controls or prior expenses changed')
    write(path,manifest)
    for name,request,_,_,_ in candidates:write(root/name/'request.json',request)
    if not args.live:print(json.dumps(manifest,ensure_ascii=False));return
    from dotenv import dotenv_values
    key=dotenv_values('/Users/ryoka/seo-pipeline/.env').get('OPENAI_API_KEY')
    if not key:raise RuntimeError('Existing provider credential unavailable')
    os.environ['OPENAI_API_KEY']=key
    root.mkdir(parents=True,exist_ok=True)
    with (root/'started.json').open('x') as f:json.dump({'pid':os.getpid()},f)
    result={'status':'running','scope':manifest['scope'],'cases':[]};write(root/'result.json',result)
    try:
        for name,request,design,text,expected in candidates:
            spent=prior_cost+sum(c.get('cost_usd',c['reserved_usd']) for c in calls(root));remaining=cap-spent
            if remaining<=0:raise RuntimeError('Combined QA cap exhausted')
            qb.LIMIT=remaining;qb.REVIEW_PHASE_LIMITS={'article':remaining,'outline':remaining}
            os.environ['QUALITY_TOTAL_LIMIT_USD']=str(remaining)
            job='reader-value-revision-'+name
            with qb.scope(job,'content_audit'):
                value,usage=checked_request(job,'tiered_article_coverage',request)
            write(root/name/'response.json',value)
            checks=fq.parse_focus(json.dumps(value,ensure_ascii=False),fq.ROLES['coverage'],text)
            findings=ef.checked_findings(value,checks,text)
            checks=ad.checked_result(checks,value,design,text)
            findings+=ef.decision_findings(value,design)
            actual='fail' if any(c['status']=='fail' for c in checks) else 'pass'
            result['cases'].append({'id':name,'expected':expected,'actual':actual,'expected_matched':actual==expected,
                'checks':checks,'editorial_findings':findings,'lead_review':value.get('lead_review'), 'input_tokens':usage.input_tokens,'output_tokens':usage.output_tokens})
            write(root/'result.json',result);print(name,actual,'expected',expected,flush=True)
        result['status']='completed'
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        current=calls(root);result.update(model_calls=len(current),new_cost_with_reservations_usd=sum(c.get('cost_usd',c['reserved_usd']) for c in current),
            combined_qa_cap_usd=cap,prior_qa_cost_usd=prior_cost,pending_calls=sum(c['status']=='pending' for c in current),
            unknown_expenses=sum(c['status']=='unknown_cost_reserved' for c in current),all_expectations_matched=bool(result['cases']) and len(result['cases'])==len(candidates) and all(c['expected_matched'] for c in result['cases']))
        write(root/'result.json',result)


if __name__=='__main__':main()
