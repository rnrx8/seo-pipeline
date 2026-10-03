"""Bounded, separately persisted subject research; no full-sheet retry rewrite."""
import json
from types import SimpleNamespace
from .ai import create_with_retry, tiered_review_enabled
from .db import get_optional_artifact, upsert_artifact
from .fresh_sources import run_with_fetch, extract_urls, normalize_url, DIRECT_POLICY, FETCH_TOOL
from .content_quality import digest, ContentQualityError


def batches(plan):
    grouped = {}
    for item in plan['items']:
        grouped.setdefault(item['subject'], []).append(item)
    result = []
    for subject, items in grouped.items():
        # Read a subject's sources once rather than starting a new agent every 8 questions.
        result.append({'subject':subject, 'items':items})
    if len(result)>24: raise ContentQualityError('調査対象が24バッチを超えました。対象範囲の確認が必要です。')
    return result


def source_fingerprint(pages, urls):
    # Same-day refetch timestamps are not new evidence; changed content is.
    return digest(json.dumps({u:({k:pages[u].get(k) for k in ('url','status','text','links','title','reason')}
        if u in pages else None) for u in urls},ensure_ascii=False,sort_keys=True))


def collection_records(job_id, loader=None):
    """New collections publish their active slots; old jobs retain all 24 slots."""
    loader=loader or get_optional_artifact
    manifest=loader(job_id,'research_collection_manifest')
    steps=json.loads(manifest['content_text'])['steps'] if manifest else [f'research_collection_{n}' for n in range(1,25)]
    if not isinstance(steps,list) or len(steps)>24 or len(set(steps))!=len(steps) or any(x not in {f'research_collection_{n}' for n in range(1,25)} for x in steps):
        raise ContentQualityError('調査記録の一覧が不正です。')
    return [r for step in steps if (r:=loader(job_id,step))]


def collect(job_id, client, *, plan, fresh, system, prompt, model, search_tool, gaps=''):
    tasks = batches(plan)
    bounded_search=tiered_review_enabled()
    if bounded_search:
        from .research_search import SourceSearch, SEARCH_TOOL, require_search_config
        require_search_config()
    tool=SEARCH_TOOL if bounded_search else {**search_tool,'max_uses':3}
    rounds=4 if bounded_search else 3
    tokens=6000 if bounded_search else 9000
    plan_hash = digest(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    gap_rows = json.loads(gaps) if gaps else []
    known_ids = {i['id'] for i in plan['items']}
    if any(g.get('id') not in known_ids for g in gap_rows):
        raise ContentQualityError('追加調査の質問IDが未特定です。全対象の再調査には広げません。')
    ids = {i['id'] for i in gap_rows}
    prior_records=collection_records(job_id) if gaps else []
    notes, queries, observed = [], [], []
    inputs = outputs = 0
    for index, task in enumerate(tasks, 1):
        step = f'research_collection_{index}'
        task_ids = {i['id'] for i in task['items']}
        prior_subject=[r for r in prior_records if r and r.get('meta',{}).get('subject')==task['subject']]
        if gaps and not (task_ids & ids):
            # These are raw notes, not a passed check. A plan edit need not
            # recollect questions the current full audit did not flag.
            preserved='\n\n'.join(dict.fromkeys(r['content_text'] for r in prior_subject))
            upsert_artifact(job_id=job_id,step=step,content_type='text/markdown',content_text=preserved,
                meta={'subject':task['subject'],'raw_notes_preserved':True,
                      'source_urls':sorted({u for r in prior_subject for u in r.get('meta',{}).get('source_urls',[])}),
                      'search_queries':list(dict.fromkeys(q for r in prior_subject for q in r.get('meta',{}).get('search_queries',[])))})
            notes.append(preserved);continue
        if gaps:
            task={**task,'items':[i for i in task['items'] if i['id'] in ids]}
            if not task['items']:continue
            task_ids={i['id'] for i in task['items']}
        request_key=digest(json.dumps({'version':'subject-bounded-search-v2','plan':plan_hash,'task':task,
            'gaps':gap_rows,'system':system+DIRECT_POLICY,'prompt':prompt,'model':model,
            'search_tool':tool,'fetch_tool':FETCH_TOOL,'max_rounds':rounds,'max_tokens':tokens},ensure_ascii=False,sort_keys=True))
        receipt=get_optional_artifact(job_id,'collection_receipt_'+request_key)
        if receipt and receipt.get('content_text','').strip() and receipt.get('meta',{}).get('result_sha256')==digest(receipt.get('content_text','')):
            if receipt['meta'].get('sources_sha256')==source_fingerprint(fresh.pages,receipt['meta'].get('source_urls',[])):
                upsert_artifact(job_id=job_id,step=step,content_type='text/markdown',
                    content_text=receipt['content_text'],meta=receipt['meta'])
                notes.append(receipt['content_text']);continue
        print(f'[research] Collecting {index}/{len(tasks)}: {task["subject"]}', flush=True)
        # List known URLs, but do not drown this subject in other subjects' bodies.
        index_context = '\n取得済み資料索引（必要な本文はfetch_current_pageで読む）\n' + json.dumps([
            {k:p.get(k) for k in ('url','title','status')} for p in fresh.pages.values()],ensure_ascii=False)
        focused = (prompt + '\n## 今回の調査担当（この対象と質問だけを調べる）\n' + json.dumps(task,ensure_ascii=False)
            + '\n必須の回答と主要な比較条件から確認する。同じ対象の関連資料をまとめて読み、補助質問ごとに個別検索を義務にしない。補助情報が関連資料にもなければ、探索済み範囲と記事から省く候補を記録し、判定は後段へ渡す。未探索・取得失敗を探索済みとしない。'
            + '\n全ファクトシートを書き直さない。担当質問ごとに回答と出典を残す。まず関連する公式資料の本文を取得する。'
              'リンク先に料金・FAQ・規約があれば必要な本文まで読む。未取得の質問は検索の要約で済ませない。'
              '公式で確認できない場合は独立した第三者本文を照合する。expert_allowedの補足説明は適切な専門家解説と資格・執筆/監修の関与を確認し、個別サービスの適法性や判決原文まで調査を拡大しない。探索した資料と不足を記録する。'
              '各事実は短い独立段落。数表全体を一つの引用にせず、事実ごとにURL・確認日・連続8〜240文字の正確な引用・判定タグを付ける。'
              '原文の引用内にさらに「」がある場合も原文を改変しない。省略記号を挿入しない。'
            + ('\n今回の不足指摘\n'+json.dumps([g for g in gap_rows if g['id'] in task_ids or g['id']=='overall'],ensure_ascii=False) if gaps else ''))
        search_args={'search_handler':SourceSearch(job_id,request_key,fresh),'max_context_chars':60000} if bounded_search else {}
        prior_urls = set(fresh.pages)
        try:
            resp, note, searches, blocks = run_with_fetch(client, create=create_with_retry, model=model,max_tokens=tokens,
                system=system, prompt=focused, search_tool=tool,fresh=fresh,
                context_override=index_context,max_rounds=rounds,**search_args)
            fresh.fetch_confirmed_citations(note)
        finally:
            fresh.save(job_id)
        if not note.strip():raise ContentQualityError('調査回答が空のため完了記録を作成しません。')
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
        # A completed collection receipt is raw evidence only; final matrix review remains mandatory.
        meta={'plan_sha256':plan_hash,'subject':task['subject'],'question_ids':sorted(task_ids),
              'source_urls':sorted(u for u in used_urls if u in fresh.pages),'search_queries':search_history,
              'result_sha256':digest(note)}
        meta['sources_sha256']=source_fingerprint(fresh.pages,meta['source_urls'])
        upsert_artifact(job_id=job_id,step='collection_receipt_'+request_key,content_type='text/markdown',content_text=note,meta=meta)
        notes.append(note); queries.extend(searches); observed.extend(blocks)
        inputs += resp.usage.input_tokens; outputs += resp.usage.output_tokens
    upsert_artifact(job_id=job_id,step='research_collection_manifest',content_type='application/json',
        content_text=json.dumps({'steps':[f'research_collection_{n}' for n in range(1,len(tasks)+1)]}),meta={'plan_sha256':plan_hash})
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=inputs,output_tokens=outputs)), '\n\n'.join(dict.fromkeys(notes)), queries, observed
