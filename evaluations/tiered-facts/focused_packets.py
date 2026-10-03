"""Offline-only routing prototype. No missing claim is accepted or dropped."""
import copy,hashlib,json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def build(captured):
    audits=[];lookup=[];store={};seen=set()
    for entry in captured:
        request=entry['request'];p=json.loads(request['messages'][0]['content'])
        questions={q['id']:q for q in p['plan']['items']}
        sources=p['sources'];by_url={s['url']:s for s in sources}
        for source in sources:store[fingerprint(source)]=copy.deepcopy(source)
        for item in p['candidate_answers']:
            q=questions[item['id']]
            if q['id'] in seen:raise ValueError('Duplicate question')
            seen.add(q['id'])
            base={'question':copy.deepcopy(q),'candidate':copy.deepcopy(item),
                  'input_fingerprint':fingerprint({'question':q,'candidate':item,'sources':sources}),
                  'origin':entry['stage'],'approved':False}
            # Unknown facts need evidence discovery, not automatic acceptance or
            # another broad Sol reading. This queue MUST still be resolved before
            # the existing whole-article coverage/required-answer gate can pass.
            if not item.get('verified'):
                lookup.append({**base,'route':'required_lookup' if q['required'] else 'optional_resolution',
                               'sources':[fingerprint(s) for s in sources],
                               'action':'Search saved bodies first; bounded new retrieval only if needed. No pass verdict.'})
                continue
            refs=item.get('evidence',[])
            if not refs or any(r.get('url') not in by_url for r in refs):
                raise ValueError('Verified claim lacks visible cited source')
            selected_urls={r['url'] for r in refs}|set(item.get('official_checked_urls',[]))
            selected=[s for s in sources if s['url'] in selected_urls]
            # Preserve whole *previously visible* pages, including truncation,
            # failed fetches, date and every adjacent excerpt. No quote windowing.
            audits.append({**base,'route':'sample' if item['basis']=='primary' else 'complex_evidence',
                           'sources':[fingerprint(s) for s in selected],
                           'available_other_sources':[fingerprint(s) for s in sources if s['url'] not in selected_urls],
                           'candidate_services':p['plan'].get('candidate_services',[]),
                           'scope_reason':p['plan'].get('scope_reason','')})
    used={k for a in audits for k in a['sources']}
    return {'audits':audits,'lookup':lookup,'source_store':store,
            'initial_audit_sources':{k:store[k] for k in sorted(used)},
            'policy':'Prototype only. A reviewer may request other stored sources; requests are not paid or answered here. Never infer absence from omitted input.'}


def audit_payload(bundle):
    # Other bodies are retained in the local store and explicitly discoverable.
    # Their metadata/catalog is sent; the full bodies would require a bounded
    # expansion step before a reviewer can resolve any resulting uncertainty.
    ids={s for a in bundle['audits'] for s in a['available_other_sources']}
    names={k:'p'+str(n) for n,k in enumerate(sorted(bundle['source_store']),1)}
    groups={}
    for key in sorted(ids):
        source=bundle['source_store'][key]
        group=(source['url'],source.get('title',''),source.get('status','success'))
        groups.setdefault(group,[]).append(names[key])
    catalog=[{'url':u,'title':t,'status':s,'available_versions':v} for (u,t,s),v in groups.items()]
    documents={}
    for key,source in bundle['initial_audit_sources'].items():
        page=copy.deepcopy(source)
        for excerpt in page.get('excerpts',[]):
            excerpt['source_ref']=names[key]+':'+excerpt['source_ref']
        documents[names[key]]=page
    audits=copy.deepcopy(bundle['audits'])
    for item in audits:
        item.pop('input_fingerprint')  # retained in local manifest, not model input
        item['sources']=[names[k] for k in item['sources']]
        item['available_other_sources']=[names[k] for k in item['available_other_sources']]
    return {'audits':audits,'source_documents':documents,
            'source_store_fingerprint':fingerprint(bundle['source_store']),
            'available_other_source_catalog':catalog,
            'policy':bundle['policy']+' Only source_ref IDs inside source_documents are visible evidence. Catalog entries alone are not evidence.'}


def resolve_reference(bundle,payload,question_id,source_ref):
    """Candidate output references cannot cross subjects or changed snapshots."""
    if payload['source_store_fingerprint']!=fingerprint(bundle['source_store']):
        raise ValueError('Source snapshot changed')
    item=next(a for a in bundle['audits'] if a['question']['id']==question_id)
    page_id=source_ref.split(':',1)[0]
    names={ 'p'+str(n):k for n,k in enumerate(sorted(bundle['source_store']),1)}
    if names.get(page_id) not in item['sources']:raise ValueError('Source not shown for this question')
    page=payload['source_documents'][page_id]
    excerpt=next(x for x in page['excerpts'] if x['source_ref']==source_ref)
    # Verify against the immutable local source, not caller-supplied quote text.
    original=bundle['source_store'][names[page_id]]
    expected_page=copy.deepcopy(original)
    for part in expected_page['excerpts']:part['source_ref']=page_id+':'+part['source_ref']
    expected=next(x for x in original['excerpts'] if x['source_ref']==source_ref.split(':',1)[1])
    if page!=expected_page:
        raise ValueError('Source text changed')
    return {'url':original['url'],'quote':expected['text']}
