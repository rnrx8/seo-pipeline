"""One transaction for concurrent reader clarification and parent-promise fixes.

Reuse genuine five-role evidence and existing patch validators. Do not move
sections or change research/reader decisions. Review every changed reader case,
opening and parent-child scope against the actual candidate independently.
"""
import copy
import json
from . import scoped_reader_completion as reader, editorial_completion as layout
from . import editorial_blueprint as bp, reader_qa, heading_answers, heading_scope
from .content_edits import content_blocks, scoped_repair_output_config
from .content_quality import ContentQualityError, digest, snapshot

POLICY='scoped-editorial-reader-completion-v2-delta-recheck'
SYSTEM=reader.SYSTEM+'''
今回は親の約束が狭いという指摘も同時に残っている。既存の関連する説明に限り、
指定した親見出しのtitle/question/conclusionと本文の見出し・冒頭を合わせて直せる。
無関係な問いを見出しへ詰め込まない。章の移動・増減・階層変更は禁止。
scope_promisesは指定親IDすべて、article_editsは指定本文IDの必要な変更だけ。
mandatory_reader_block_idsはすべて直す。他の章や資料の情報は足さない。
paragraph_limits以内で、実際の暮らしが伝わる自然な文章にまとめる。JSONのみ。'''

def fail(message):raise ContentQualityError('章内の統合編集: '+message)

def policy_digest():
    from .ai import get_step_config
    return digest(json.dumps([POLICY,SYSTEM,reader.REVIEW,reader.SYSTEM,reader.BUDGET_POLICY,
        layout.REVIEW,heading_scope.POLICY,reader_qa.reader_journey.REVIEW_POLICY,
        get_step_config('content_repair'),get_step_config('content_audit')],ensure_ascii=False,sort_keys=True))

def packet(article,outline,report,context):
    base=reader.packet(report,context,allow_layout=True)
    design=context['requirements']['article_design']
    scopes=heading_scope.groups(context['text'],context['outline'],design)
    parents=[s['id'] for s in scopes if any(report['phases']['organization']['heading_scope_reviews'][s['id']][c['id']]['relation']=='next_decision' for c in s['children'])]
    if not parents:fail('親の回答範囲の指摘がない。')
    if not any(f.get('role')=='coverage' for f in base['findings']):fail('章の生活価値の指摘がない。')
    p=layout.packet(article,outline,report,context)
    from .prose_completion import plain_paragraph
    blocks={b['id']:b['text'] for b in p['article_blocks']}
    # Keep actual finding paragraphs plus parent headings/openings only. Do not
    # inherit the broader layout editor's permission to alter other ancestors.
    targets={i for i in base['target_block_ids'] if plain_paragraph(blocks[i])}
    mandatory={i for f in base['findings'] if f.get('role')=='coverage' for i in f['block_ids']}
    for h in heading_answers.entries(context['text']):
        if p['owners'].get(h['heading_block_ids'][0]) in parents:
            targets.update(h['opening_block_ids']);targets.add(h['heading_block_ids'][0])
    if not mandatory<=targets:fail('読者価値の修正対象が通常段落ではない。')
    p.update(target_block_ids=sorted(targets),target_section_ids=parents,
        mandatory_reader_block_ids=sorted(mandatory),article_design=base['article_design'])
    p['scope_promises']={s['id']:{k:s[k] for k in ('title','question','conclusion')} for s in p['blueprint']['sections'] if s['id'] in parents}
    p['paragraph_limits']=reader.paragraph_limits(context['text'],{**p,'target_block_ids':[i for i in targets if plain_paragraph(blocks[i])]})
    return p

def eligible(article,outline,report,context):
    try:packet(article,outline,report,context);return True
    except (ContentQualityError,KeyError,TypeError,ValueError,StopIteration):return False

def apply(article,outline,report,context,proposal):
    p=packet(article,outline,report,context)
    if set(proposal)!={'article_edits','scope_promises'} or set(proposal['scope_promises'])!=set(p['target_section_ids']):fail('親の変更対象が不整合。')
    edits=proposal['article_edits'];ids=[e.get('id') for e in edits]
    if len(ids)!=len(set(ids)) or not set(ids)<=set(p['target_block_ids']):fail('本文の対象が重複・拡大。')
    if any(not set(f['block_ids'])&set(ids) for f in p['findings'] if f.get('role')=='coverage'):fail('未対応の章の指摘がある。')
    old=bp.require_projection(outline);patch=[]
    for sid,value in proposal['scope_promises'].items():
        if set(value)!={'title','question','conclusion'}:fail('根拠・読者設計は変更不可。')
        section=copy.deepcopy(next(s for s in old['sections'] if s['id']==sid));section.update(value)
        patch.append({'id':sid,'section':section})
    converted={'article_edits':edits,'outline_patch':{'edits':patch,'delete_ids':[],'section_order':[]}}
    text,revised,binding=layout.apply_proposal(article,outline,report,context,converted)
    # No new paragraphs, H4s or other block renumbering are permitted here.
    old_blocks=content_blocks(context['text']);new_blocks=content_blocks(text)
    if [b['id'] for b in old_blocks]!=[b['id'] for b in new_blocks]:fail('段落が増減。')
    for before,after in zip(old_blocks,new_blocks):
        if before['id'] in ids and not before['text'].startswith('#') and not reader.plain_paragraph(after['text']):fail('段落の形式が変更。')
    return text,revised,binding

def view(article,outline,report,context,proposal,text,revised,binding,*,include_flagged_scopes=True):
    current=layout.candidate_context(context,revised,binding);current['text']=text
    def cases(c):return {s['id']:s for batch in reader_qa.packets(c['text'],c['outline'],c['requirements']['article_design']) for s in batch['cases']}
    before,after=cases(context),cases(current)
    old_headings={h['id']:h for h in heading_answers.entries(context['text'])}
    old_scopes={h['id']:h for h in heading_scope.groups(context['text'],context['outline'],context['requirements']['article_design'])}
    result={'article_before':context['text'],'article_after':text,'article_design':current['requirements']['article_design'],
        'findings':report['editorial_findings'],'scope_promises':proposal['scope_promises'],
        'changed_blocks':proposal['article_edits'],'reader_cases':{'cases':[after[i] for i in after if after[i]!=before[i]]},
        'heading_answer_entries':[h for h in heading_answers.entries(text) if h!=old_headings[h['id']]],
        'heading_scope_groups':[h for h in heading_scope.groups(text,current['outline'],current['requirements']['article_design']) if h!=old_scopes[h['id']] or include_flagged_scopes and h['id'] in proposal['scope_promises']]}
    return result,current

def review_config(v):
    return heading_scope.output_config(reader.review_config(v),v['heading_scope_groups'])

def require_review(verdict,v,base,context,text):
    reader.require_review({k:x for k,x in verdict.items() if k!='heading_scope_reviews'},v,base,context,text)
    if heading_scope.checked(verdict,v['heading_scope_groups'],text):fail('親子の回答範囲が未解決。')

def complete(job_id,client,*,article,outline,report,context,check_candidate):
    from .ai import get_step_config
    from .tiered_research import checked_request
    from .db import upsert_artifact
    p=packet(article,outline,report,context)
    promises=bp.obj({i:bp.obj({k:bp.STRING for k in ('title','question','conclusion')}) for i in p['target_section_ids']})
    schema=bp.obj({'article_edits':scoped_repair_output_config(p['target_block_ids'])['format']['schema']['properties']['edits'],'scope_promises':promises})
    model,limit=get_step_config('content_repair')
    request=dict(model=model,max_tokens=limit,system=SYSTEM,
        messages=[{'role':'user','content':json.dumps(p,ensure_ascii=False)}],output_config=bp.model_output_config(schema),output_budget_includes_thinking=True)
    from .db import get_optional_artifact
    from .tiered_research import cached_result
    # An interrupted response is never sent again. Its complete edits and cost
    # remain frozen; a separate suffix supplies only the absent fields/IDs.
    partial=get_optional_artifact(job_id,'tiered_scoped_edit_repair_incomplete')
    if partial:
        saved_limit=partial.get('meta',{}).get('max_tokens')
        if saved_limit not in (limit,max(limit,10000)):fail('未完成応答の出力枠が不明。')
        request['max_tokens']=saved_limit
    proposal=cached_result(job_id,'tiered_scoped_edit_repair',request)
    if proposal is None and partial:
        from .scoped_edit_suffix import recover
        proposal=recover(job_id,request,partial,p,client)
    elif proposal is None:
        request['max_tokens']=max(limit,10000)
        proposal,_=checked_request(job_id,'tiered_scoped_edit_repair',request,client=client)
    text,revised,binding=apply(article,outline,report,context,proposal)
    check_candidate(text,revised,binding)
    v,current=view(article,outline,report,context,proposal,text,revised,binding)
    receipt={'policy':POLICY,'policy_sha256':policy_digest(),'base_article':article,'base_outline':outline,
        'base_context':context,'base_report':report,'proposal':proposal}
    upsert_artifact(job_id=job_id,step='scoped_edit_candidate',content_type='application/json',content_text=json.dumps(receipt,ensure_ascii=False),meta={'audited':False})
    model,limit=get_step_config('content_audit')
    def review_request(value):return dict(model=model,max_tokens=max(limit,10000),
        system=reader.REVIEW+'\n'+reader_qa.CONTEXT_POLICY+'\n'+reader_qa.reader_journey.REVIEW_POLICY+'\n'+heading_answers.POLICY+'\n'+heading_scope.POLICY,
        messages=[{'role':'user','content':json.dumps(value,ensure_ascii=False)}],output_config=review_config(value))
    legacy,_=view(article,outline,report,context,proposal,text,revised,binding,include_flagged_scopes=False)
    from .tiered_research import structured_review_output_limit
    legacy_request=review_request(legacy);legacy_request['max_tokens']=structured_review_output_limit(legacy_request)
    verdict=cached_result(job_id,'tiered_scoped_edit_review',legacy_request)
    if verdict is not None:v=legacy
    else:verdict,_=checked_request(job_id,'tiered_scoped_edit_review',review_request(v))
    try:require_review(verdict,v,report,context,text)
    except ContentQualityError:
        if not any(c.get('status')=='fail' for c in verdict.get('checks',{}).values()):raise
        from .scoped_edit_correction import correct
        proposal,text,revised,binding,verdict,v,correction=correct(job_id,client,article,outline,report,context,proposal,verdict,v,check_candidate)
        current=layout.candidate_context(context,revised,binding);current['text']=text
        receipt.update(proposal=proposal,correction=correction)
    receipt.update(review=verdict,review_input_sha256=digest(json.dumps(v,ensure_ascii=False,sort_keys=True)))
    result=copy.deepcopy(report);result.update(valid=True,snapshot=snapshot(**current),acceptance_mode='full_audit_plus_scoped_editorial_reader_review',scoped_edit_completion=receipt)
    for c in result['checks']:
        if c['status']=='fail':c.update(status='pass',reason='元の全文QAを保持し、章内編集と影響する全読者ケース・見出し・親子関係を独立確認。',affected_blocks=[])
    return text,revised,binding,result

def validate_completion(report,expected,context):
    try:
        if any(report.get(k) for k in ('local_completion','editorial_completion','scoped_reader_completion')):fail('別の受入記録が混在。')
        r=report['scoped_edit_completion'];base=r['base_context'];raw=r['base_report']
        if r['policy']!=POLICY or r['policy_sha256']!=policy_digest():fail('確認方針が不一致。')
        text,revised,binding=apply(r['base_article'],r['base_outline'],raw,base,r['proposal'])
        v,current=view(r['base_article'],r['base_outline'],raw,base,r['proposal'],text,revised,binding)
        if current!=context or snapshot(**current)!=expected:fail('資料・本文・構成が確認後に変更。')
        if report['phases']!=raw['phases'] or report['editorial_findings']!=raw['editorial_findings']:fail('元の指摘が変更。')
        if report.get('acceptance_mode')!='full_audit_plus_scoped_editorial_reader_review':fail('受入方式が不整合。')
        if len(report['checks'])!=len(raw['checks']):fail('合否項目が増減。')
        for old,new in zip(raw['checks'],report['checks']):
            if old['key']!=new['key'] or old['status']!='fail' and old!=new or old['status']=='fail' and new['status']!='pass':fail('合否の対応が不整合。')
        if r['review_input_sha256']!=digest(json.dumps(v,ensure_ascii=False,sort_keys=True)):fail('確認資料が不一致。')
        if r.get('correction'):
            from .scoped_edit_correction import validate
            validate(r,context,v)
        require_review(r['review'],v,raw,base,text)
    except (KeyError,ValueError,TypeError) as exc:fail('保存記録が不足・不整合。')
