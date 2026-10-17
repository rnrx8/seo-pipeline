"""Evaluation-only reuse of same-day acquisition data, never old QA verdicts."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source, settings, checked_on):
    source=Path(source).resolve()
    if json.loads((source/'settings.json').read_text())!=settings:
        raise ValueError('Research reuse requires the same frozen user settings')
    artifacts=source/'artifacts'
    manifest=json.loads(json.loads((artifacts/'research_collection_manifest.json').read_text())['content_text'])
    notes=manifest['steps']
    if (not notes or len(notes)>24 or len(notes)!=len(set(notes))
        or any(not re.fullmatch(r'research_collection_(?:[1-9]|1[0-9]|2[0-4])',name) for name in notes)):
        raise ValueError('Invalid raw-note manifest')
    mapping={'serp':'serp','competitor_evidence':'competitor_evidence','fresh_sources':'fresh_sources',
             'fact_sheet':'research_draft','research_collection_manifest':'research_collection_manifest',
             **{name:name for name in notes}}
    pages=json.loads(json.loads((artifacts/'fresh_sources.json').read_text())['content_text'])
    for page in pages:
        date=datetime.fromisoformat(page['fetched_at']).astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()
        if date!=checked_on:raise ValueError('Reuse requires same-day acquired sources; do not relabel old sources')
    draft=json.loads((artifacts/'research_draft.json').read_text())
    if not draft['content_text'].strip() or draft.get('meta',{}).get('canonical_research_answers'):
        raise ValueError('Only noncanonical raw notes may seed the new research input')
    return {'scope':'Same-day SERP, competitor acquisition, source bodies and raw research notes only',
            'source_root':str(source),'checked_on':checked_on,'mapping':mapping,
            'files_sha256':{name:digest(artifacts/(name+'.json')) for name in mapping.values()},
            'pages':len(pages),'successful_pages':sum(p['status']=='success' for p in pages),
            'excluded':'No old reader design, research plan/matrix, outline, article, review or cached acceptance'}


def seed(spec, *, job_id, save):
    artifacts=Path(spec['source_root'])/'artifacts'
    for name,expected in spec['files_sha256'].items():
        if digest(artifacts/(name+'.json'))!=expected:raise ValueError('Frozen reuse source changed')
    for target,name in spec['mapping'].items():
        record=json.loads((artifacts/(name+'.json')).read_text())
        text=record['content_text'];meta={}
        if target=='fresh_sources':
            pages=json.loads(text)
            for page in pages:page.pop('evidence_quotes',None)
            text=json.dumps(pages,ensure_ascii=False)
        elif target.startswith('research_collection_') and target!='research_collection_manifest':
            meta={k:copy.deepcopy(record.get('meta',{}).get(k)) for k in ('subject','source_urls','search_queries')}
        elif target=='serp':
            meta={'source':copy.deepcopy(record.get('meta',{}).get('source'))}
        meta['research_reuse']={'source_job_id':record['job_id'],'source_step':name,
            'source_file_sha256':spec['files_sha256'][name],'acceptance_inherited':False}
        save(job_id=job_id,step=target,content_type=record['content_type'],content_text=text,meta=meta)


def install(spec, *, job_id, get, save, receipt):
    """Keep normal analysis/verification/writing. Only acquisition stages reuse input."""
    from pipeline import step_serp,step_fact_sheet
    original_fact=step_fact_sheet.run
    def serp(ident, keyword, api_key=None):
        if ident!=job_id:raise ValueError('Unexpected research-reuse job')
        seed(spec,job_id=ident,save=save)
        receipt({'status':'raw_input_reused','acceptance_inherited':False,'spec':spec})
        return get(ident,'serp')
    def facts(ident, keyword, api_key=None, research_gaps='', research_question_ids=None):
        if ident!=job_id:raise ValueError('Unexpected research-reuse job')
        if research_gaps or research_question_ids is not None:
            return original_fact(ident,keyword,api_key=api_key,research_gaps=research_gaps,research_question_ids=research_question_ids)
        return get(ident,'fact_sheet')
    step_serp.run=serp;step_fact_sheet.run=facts
