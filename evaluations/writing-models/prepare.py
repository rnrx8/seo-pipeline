"""Offline, frozen-input writing comparison. Never calls a model or writes production."""
import argparse, hashlib, json, re, socket
from pathlib import Path
from unittest.mock import patch
from pipeline import step_article as writer
from pipeline.generation_context import decision_bundle
from pipeline.content_contract import contract_prompt
from pipeline.step_review import _build_learned_rules_block


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def prepare(artifacts_path, settings_path, output):
    rows=json.loads(Path(artifacts_path).read_text())
    a={row['step']:row for row in rows}
    settings=json.loads(Path(settings_path).read_text());job=settings['job']
    outline=a['outline']['content_text']
    headings=list(re.finditer(r'^#{2,5} H[234].*',outline,re.M))
    h3s=[m for m in headings if m.group().startswith('#### H3')]
    assert len(h3s)>=3
    excerpt=outline[:h3s[2].start()]
    evidence=decision_bundle(json.loads(a['research_plan']['content_text']),json.loads(a['research_matrix']['content_text']),quotes=True)
    chains=json.loads(a['intent_chains']['content_text']).get('chains',[])
    context=writer.USER_TEMPLATE.format(keyword=job['main_keyword'],intent_text=a['search_intent']['content_text'],outline_text=excerpt,fact_text=evidence)
    context+=contract_prompt(json.loads(a['content_contract']['content_text']))
    context+=writer._build_chains_prompt(chains)+writer._build_extra_instructions(job)
    context+=writer._build_company_prompt(settings['companies'],job.get('company_restriction','ai'))
    if settings.get('service'):context+=writer._build_service_prompt(settings['service'])
    context+=_build_learned_rules_block(settings.get('learned_rules',[]))
    context+='''\n【比較実験の出力範囲】今回は記事冒頭の抜粋を生成する。H1、リード、最初の比較H2直下（5社の比較を維持）、既婚者クラブとヒールメイトの2つのH3まで。3社目以降や後続H2・まとめ・CTAは出力しない。記事全体の構造契約は背景であり、この指定範囲以外を生成する指示ではない。未出力の章を欠落として埋めず、今回の2商材の説明は書き切る。構成の内容・既存の執筆品質基準に従い、読者心理の新しい分析項目は追加しない。Markdown本文のみを出力する。'''
    common={'system':writer.SYSTEM_PROMPT,'messages':[{'role':'user','content':context}],'max_tokens':10000,'output_config':{'effort':'medium'}}
    requests={name:{**common,'model':model} for name,model in [('opus','claude-opus-5-5'),('sonnet','claude-sonnet-5-5')]}
    assert {k:v for k,v in requests['opus'].items() if k!='model'}=={k:v for k,v in requests['sonnet'].items() if k!='model'}
    out=Path(output);out.mkdir(exist_ok=True,parents=True)
    for name,request in requests.items():(out/f'{name}-request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2))
    costs=[]
    for name,request in requests.items():
        size=len(json.dumps(request,ensure_ascii=False).encode())+4096
        ir,orr=(4,20) if name=='opus' else (2,10)
        costs.append({'arm':name,'max_output_tokens_including_thinking':10000,'input_byte_bound':size,'uncached_byte_ceiling_jpy':round((size*ir+10000*orr)/1e6*200,2)})
    manifest={'status':'prepared_not_executed','paid_calls':0,'source_job':job['id'],'source_artifact_hashes':{k:digest(a[k]['content_text']) for k in ['outline','research_plan','research_matrix','search_intent','intent_chains','content_contract']},'settings_hash':digest(Path(settings_path).read_text()),'shared_request_hash':digest(json.dumps(common,ensure_ascii=False,sort_keys=True)),'scope':'lead + comparison H2 intro/table + first two product H3s','same_effort':'medium','historical_opus_is_reference_only':True,'prices_source':'https://platform.claude.com/docs/en/about-claude/pricing','yen_per_usd':200,'costs':costs,'generation_requests':2,'automatic_retries':0,'automatic_deployment':False,'third_arm':'Optional one Opus block-edit request from the SAME Sonnet draft; no repeated Sonnet generation. Scope/cost bounded before approval.','limitations':['One topic and one sample per model do not establish general superiority.','No full-article coherence or total production cost conclusion from this excerpt.','No synthetic judge pass is a real quality result.']}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps({'status':manifest['status'],'costs':costs},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',required=True);p.add_argument('--settings',required=True);p.add_argument('--output',required=True);v=p.parse_args()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline preparation only')),patch.object(socket,'create_connection',side_effect=AssertionError('Offline preparation only')):
        prepare(v.artifacts,v.settings,v.output)
