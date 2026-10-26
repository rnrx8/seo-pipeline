"""A single source-bound correction; reuse only identical prior review cases."""
import copy,json,re
from . import scoped_edit_completion as finish,reader_qa,heading_answers
from .content_quality import ContentQualityError,digest

def sha(value):return digest(json.dumps(value,ensure_ascii=False,sort_keys=True))

def delta(old_view,new_view):
    v=copy.deepcopy(new_view)
    if 'article_design' in v:
        # Every affected native case already carries its own adopted design.
        # Avoid a second copy of all unrelated section plans in the delta.
        v['article_design']={k:x for k,x in v['article_design'].items() if k not in ('sections','section_binding')}
    old={c['id']:c for c in old_view['reader_cases']['cases']}
    v['reader_cases']['cases']=[c for c in v['reader_cases']['cases'] if c!=old.get(c['id'])]
    old={c['id']:c for c in old_view['heading_answer_entries']}
    v['heading_answer_entries']=[c for c in v['heading_answer_entries'] if c!=old.get(c['id'])]
    return v

def compose(previous,old_view,review,v,new_view):
    result=copy.deepcopy(review)
    for field,case_key,view_key in (('reader_journeys','reader_cases','cases'),('heading_answer_reviews','heading_answer_entries',None)):
        old_cases=old_view[case_key][view_key] if view_key else old_view[case_key]
        new_cases=new_view[case_key][view_key] if view_key else new_view[case_key]
        checked_cases=v[case_key][view_key] if view_key else v[case_key]
        old={c['id']:c for c in old_cases};checked={c['id'] for c in checked_cases};rows={}
        for c in new_cases:
            i=c['id']
            if i in checked:rows[i]=review[field][i]
            elif c==old.get(i):rows[i]=previous[field][i]
            else:raise ContentQualityError('変更した確認対象の再確認が不足しています。')
        result[field]=rows
    return result

def correct(job,client,article,outline,report,context,proposal,verdict,old_view,check_candidate):
    from .db import get_optional_artifact,upsert_artifact
    from .tiered_research import checked_request
    from .ai import get_step_config
    p=finish.packet(article,outline,report,context)
    reasons='\n'.join(c['reason'] for c in verdict['checks'].values() if c['status']=='fail')
    targets=set(re.findall(r'block-\d+',reasons))&set(p['target_block_ids'])
    for h in heading_answers.entries(context['text']):
        if p['owners'].get(h['heading_block_ids'][0]) in p['target_section_ids']:
            targets.update(h['heading_block_ids']+h['opening_block_ids'])
    if not targets:raise ContentQualityError('局所修正の対象が特定できません。')
    operator=get_optional_artifact(job,'scoped_edit_correction_candidate')
    source='native_model'
    if operator:
        candidate=json.loads(operator['content_text'])
        if candidate['base_proposal_sha256']!=sha(proposal) or candidate['base_review_sha256']!=sha(verdict):raise ContentQualityError('保存修正案の元資料が不一致です。')
        fixed=candidate['proposal'];source='Codex_condition_restoration_and_parent_promise'
    else:
        bp=finish.bp
        schema=bp.obj({'article_edits':finish.scoped_repair_output_config(sorted(targets))['format']['schema']['properties']['edits'],
            'scope_promises':bp.obj({i:bp.obj({k:bp.STRING for k in ('title','question','conclusion')}) for i in p['target_section_ids']})})
        payload={'article_before':context['text'],'current_edits':proposal,'failed_checks':{k:v for k,v in verdict['checks'].items() if v['status']=='fail'},
            'target_block_ids':sorted(targets),'paragraph_limits':{i:n for i,n in p['paragraph_limits'].items() if i in targets},'reader_focus':p['article_design']['reader_focus']}
        model,_=get_step_config('content_repair')
        patch,_=checked_request(job,'tiered_scoped_edit_correction',dict(model=model,max_tokens=6000,
            system='直前の独立確認で不合格だった箇所だけを修正する。元の条件を戻し、新しい事実を削除できる。対象外の合格文は保持する。親の問いと見出しは実際の関連する説明に合わせる。移動・新情報・免責は禁止。元の文に戻す段落も返してよい。JSONのみ。',
            messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],output_config=bp.model_output_config(schema),output_budget_includes_thinking=True),client=client)
        edits={e['id']:e for e in proposal['article_edits']};edits.update({e['id']:e for e in patch['article_edits']})
        old={b['id']:b['text'] for b in p['article_blocks']}
        fixed={'article_edits':[e for e in edits.values() if e['new']!=old[e['id']]],'scope_promises':patch['scope_promises']}
    old={e['id']:e['new'] for e in proposal['article_edits']};new={e['id']:e['new'] for e in fixed['article_edits']}
    if any(old.get(i)!=new.get(i) for i in set(old)|set(new) if i not in targets):raise ContentQualityError('合格済みの対象外文章が変わっています。')
    text,revised,binding=finish.apply(article,outline,report,context,fixed);check_candidate(text,revised,binding)
    final_view,current=finish.view(article,outline,report,context,fixed,text,revised,binding)
    v=delta(old_view,final_view)
    from .ai import get_step_config
    model,_=get_step_config('content_audit')
    review,_=checked_request(job,'tiered_scoped_edit_recheck',dict(model=model,max_tokens=6000,
        system=finish.reader.REVIEW+'\n'+reader_qa.CONTEXT_POLICY+'\n'+reader_qa.reader_journey.REVIEW_POLICY+'\n'+heading_answers.POLICY+'\n'+finish.heading_scope.POLICY,
        messages=[{'role':'user','content':json.dumps(v,ensure_ascii=False)}],output_config=finish.review_config(v)))
    merged=compose(verdict,old_view,review,v,final_view)
    finish.require_review(merged,final_view,report,context,text)
    receipt={'source':source,'initial_proposal':proposal,'initial_review':verdict,'initial_view_sha256':sha(old_view),
        'delta_review':review,'delta_view_sha256':sha(v),'correction_target_ids':sorted(targets)}
    return fixed,text,revised,binding,merged,final_view,receipt

def validate(receipt,current,final_view):
    c=receipt['correction'];article=receipt['base_article'];outline=receipt['base_outline'];raw=receipt['base_report'];base=receipt['base_context']
    t,o,b=finish.apply(article,outline,raw,base,c['initial_proposal'])
    prior,_=finish.view(article,outline,raw,base,c['initial_proposal'],t,o,b,include_flagged_scopes=False)
    if sha(prior)!=c['initial_view_sha256']:
        prior,_=finish.view(article,outline,raw,base,c['initial_proposal'],t,o,b)
    v=delta(prior,final_view)
    if sha(prior)!=c['initial_view_sha256'] or sha(v)!=c['delta_view_sha256']:raise ContentQualityError('局所再確認の保存入力が不一致です。')
    old={e['id']:e['new'] for e in c['initial_proposal']['article_edits']};new={e['id']:e['new'] for e in receipt['proposal']['article_edits']}
    if any(old.get(i)!=new.get(i) for i in set(old)|set(new) if i not in c['correction_target_ids']):raise ContentQualityError('再確認の対象外文章が変わっています。')
    if compose(c['initial_review'],prior,c['delta_review'],v,final_view)!=receipt['review']:raise ContentQualityError('保存済みの個別確認と合成結果が不一致です。')
