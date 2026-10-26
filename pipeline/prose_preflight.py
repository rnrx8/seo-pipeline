"""Fix known prose-run overflow once before paying for semantic article QA.

This produces an unaudited candidate, never an acceptance receipt. The ordinary
five roles still review the entire resulting article, including all edits.
"""
import json
from .content_quality import ContentQualityError
from .content_edits import content_blocks,scoped_repair_output_config
from .prose_completion import plain_paragraph
from .scoped_reader_completion import paragraph_limits,apply
from .editorial_completion import MECHANICAL_SYSTEM,immutable_anchors

def complete(job_id,client,*,text,check_candidate):
    from .readability import readability_issues
    issues=readability_issues(text)
    if not issues:return text
    if any(i['key']!='continuous_prose' for i in issues):return text
    blocks=content_blocks(text);plain={b['id'] for b in blocks if plain_paragraph(b['text'])}
    by_id={b['id']:b['text'] for b in blocks};targets=set()
    for issue in issues:
        choices={l['id'] for l in issue['affected_blocks']} & plain
        if not choices:raise ContentQualityError('文章量の整理を通常段落へ限定できません。原稿を保持しました。')
        targets.add(max(sorted(choices),key=lambda bid:len(by_id[bid])))
    packet={'article_blocks':blocks,'target_block_ids':sorted(targets)}
    payload={**packet,'paragraph_limits':paragraph_limits(text,packet),'mechanical_issues':issues}
    from .ai import get_step_config
    from .tiered_research import checked_request
    model,_=get_step_config('content_repair')
    proposal,_=checked_request(job_id,'tiered_article_prose_preflight',dict(model=model,max_tokens=6000,
        system=MECHANICAL_SYSTEM+'\n全対象段落をparagraph_limits以内に実際に短くし、editsへ返す。語尾や共感だけを削らず、既存の事実・条件・必要回答・読者理解を保持する。',
        messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
        output_config=scoped_repair_output_config(packet['target_block_ids']),output_budget_includes_thinking=True),client=client)
    # Numbers/links/quotations are immutable. Counting words like 必要/ない
    # cannot establish meaning preservation; the subsequent whole QA must do so.
    candidate=apply(text,proposal,packet,anchor_fn=immutable_anchors);check_candidate(candidate)
    from .db import upsert_artifact
    upsert_artifact(job_id=job_id,step='article_prose_preflight',content_type='application/json',
        content_text=json.dumps({'origin':'mechanical_preflight_not_semantic_QA','base_text':text,'candidate_text':candidate,
            'issues':issues,'proposal':proposal,'semantic_quality_verified':False},ensure_ascii=False),meta={'audited':False})
    return candidate
