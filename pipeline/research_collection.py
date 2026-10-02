"""Bounded, separately persisted subject research; no full-sheet retry rewrite."""
import json
from types import SimpleNamespace
from .ai import create_with_retry
from .db import get_optional_artifact, upsert_artifact
from .fresh_sources import run_with_fetch, extract_urls, normalize_url
from .content_quality import digest, ContentQualityError


def batches(plan):
    grouped = {}
    for item in plan['items']:
        grouped.setdefault(item['subject'], []).append(item)
    result = []
    for subject, items in grouped.items():
        # Keep each pass focused even when a plan has many distinct subquestions.
        for start in range(0, len(items), 8):
            result.append({'subject':subject, 'items':items[start:start+8]})
    if len(result)>24: raise ContentQualityError('調査対象が24バッチを超えました。対象範囲の確認が必要です。')
    return result


def collect(job_id, client, *, plan, fresh, system, prompt, model, search_tool, gaps=''):
    tasks = batches(plan)
    plan_hash = digest(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    gap_rows = json.loads(gaps) if gaps else []
    known_ids = {i['id'] for i in plan['items']}
    # Post-outline audits report check keys, not question IDs. Never silently
    # reuse every batch or crash when those existing callers request research.
    gap_rows = [g if g.get('id') in known_ids else {**g, 'id':'overall'} for g in gap_rows]
    ids = {i['id'] for i in gap_rows}
    prior_records=[get_optional_artifact(job_id,f'research_collection_{n}') for n in range(1,25)] if gaps else []
    notes, queries, observed = [], [], []
    inputs = outputs = 0
    for index, task in enumerate(tasks, 1):
        step = f'research_collection_{index}'
        task_ids = {i['id'] for i in task['items']}
        prior_subject=[r for r in prior_records if r and r.get('meta',{}).get('subject')==task['subject']]
        if gaps and 'overall' not in ids and not (task_ids & ids) and prior_subject:
            # These are raw notes, not a passed check. A plan edit need not
            # recollect questions the current full audit did not flag.
            notes.extend(r['content_text'] for r in prior_subject); continue
        if gaps and 'overall' not in ids:
            task={**task,'items':[i for i in task['items'] if i['id'] in ids]}
            if not task['items']:continue
            task_ids={i['id'] for i in task['items']}
        print(f'[research] Collecting {index}/{len(tasks)}: {task["subject"]}', flush=True)
        # List known URLs, but do not drown this subject in other subjects' bodies.
        index_context = '\n取得済み資料索引（必要な本文はfetch_current_pageで読む）\n' + json.dumps([
            {k:p.get(k) for k in ('url','title','status')} for p in fresh.pages.values()],ensure_ascii=False)
        focused = (prompt + '\n## 今回の調査担当（この対象と質問だけを調べる）\n' + json.dumps(task,ensure_ascii=False)
            + '\n全ファクトシートを書き直さない。担当質問ごとに回答と出典を残す。まず関連する公式資料の本文を取得する。'
              'リンク先に料金・FAQ・規約があれば必要な本文まで読む。未取得の質問は検索の要約で済ませない。'
              '公式で確認できない場合は独立した第三者本文を照合する。expert_allowedの補足説明は適切な専門家解説と資格・執筆/監修の関与を確認し、個別サービスの適法性や判決原文まで調査を拡大しない。探索した資料と不足を記録する。'
              '各事実は短い独立段落。数表全体を一つの引用にせず、事実ごとにURL・確認日・連続8〜240文字の正確な引用・判定タグを付ける。'
              '原文の引用内にさらに「」がある場合も原文を改変しない。省略記号を挿入しない。'
            + ('\n今回の不足指摘\n'+json.dumps([g for g in gap_rows if g['id'] in task_ids or g['id']=='overall'],ensure_ascii=False) if gaps else ''))
        prior_urls = set(fresh.pages)
        try:
            resp, note, searches, blocks = run_with_fetch(client, create=create_with_retry, model=model,max_tokens=9000,
                system=system, prompt=focused, search_tool={**search_tool,'max_uses':8},fresh=fresh,
                context_override=index_context,max_rounds=8)
            fresh.fetch_confirmed_citations(note)
        finally:
            fresh.save(job_id)
        used_urls = set(extract_urls(note)) | (set(fresh.pages)-prior_urls)
        for old_subject in prior_subject:
            # A focused retry may omit previously answered questions from its note.
            # Keep their original source lineage, not their acceptance verdict.
            used_urls.update(old_subject.get('meta',{}).get('source_urls',[]))
            used_urls.update(extract_urls(old_subject.get('content_text','')))
        used_urls.update(normalize_url((getattr(b,'input',None) or {}).get('url','')) for b in blocks if getattr(b,'name','')=='fetch_current_page')
        previous_searches=[query for r in prior_subject for query in r.get('meta',{}).get('search_queries',[])]
        search_history=list(dict.fromkeys([*previous_searches,*searches]))
        upsert_artifact(job_id=job_id,step=step,content_type='text/markdown',content_text=note,
            meta={'plan_sha256':plan_hash,'subject':task['subject'],'question_ids':sorted(task_ids),'targeted_retry':bool(gaps),'source_urls':sorted(u for u in used_urls if u in fresh.pages),
                  'input_tokens':resp.usage.input_tokens,'output_tokens':resp.usage.output_tokens,'search_queries':search_history})
        notes.append(note); queries.extend(searches); observed.extend(blocks)
        inputs += resp.usage.input_tokens; outputs += resp.usage.output_tokens
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=inputs,output_tokens=outputs)), '\n\n'.join(dict.fromkeys(notes)), queries, observed
