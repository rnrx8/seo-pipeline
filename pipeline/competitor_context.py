"""Bounded competitor excerpts and honest SERP enrichment availability."""
import copy
import json
import re
from .content_quality import ContentQualityError, digest

VERSION='competitor-evidence-v1'
GAP_POLICY='''
【読者の問いと競合の回答】
competitor_contextは今回取得した競合本文の関連抜粋。見出しの有無だけで答えの十分さを判定しない。採用する読者の問いごとに、実際の回答・条件・具体性を確認し、content_gapsへanswered_in_excerpt/gap_candidate/not_assessedを記録する。
gap_candidateは確認した範囲で不足・曖昧な回答がある場合だけ。抜粋に見当たらないことを記事全体や検索上位全体に情報がない証明にせず、不足候補としてquestionとopportunityを具体化する。本文未取得や対応箇所が未提示ならnot_assessed。既存の必要な回答を残し、違うこと自体を目的にしない。
引用は提示excerptsのidと短い連続した原文を返す。取得失敗、見出し、検索スニペットを競合本文の引用にしない。research_idsは必要な調査質問に結ぶ。PAA・関連検索の未取得・未対応・取得失敗・0件の区別も維持し、欠損を他クエリのデータで補わない。
'''


def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
STR={'type':'string'}
GAP_SCHEMA={'type':'array','items':obj({'need_id':STR,'question':STR,
    'status':{'type':'string','enum':['answered_in_excerpt','gap_candidate','not_assessed']},
    'reason':STR,'opportunity':STR,'research_ids':{'type':'array','items':STR},
    'evidence':{'type':'array','items':obj({'excerpt_id':STR,'quote':STR})}})}


def enrichment(raw, keyword, *, browser=False, keys=None):
    keys=keys or {'people_also_ask':('people_also_ask','related_questions'), 'related_searches':('related_searches',)}
    result={}
    declared=raw.get('enrichment_status',{})
    if not isinstance(declared,dict):declared={}
    for name,aliases in keys.items():
        state=declared.get(name,{})
        if not isinstance(state,dict):state={}
        explicit=state.get('status')
        key=next((k for k in aliases if k in raw),None)
        value=raw.get(key) if key else None
        valid=isinstance(value,list)
        if state.get('query') and state['query']!=keyword:
            status='query_mismatch';value=[]
        elif explicit in ('not_supported','fetch_failed','not_captured'):
            status=explicit;value=[]
        elif valid and value:
            status='collected'
        elif valid:
            status=('collected_empty' if not browser or explicit in ('collected','collected_empty')
                    and state.get('query')==keyword else 'unverified_empty')
        else:
            status='not_captured' if browser else 'not_returned';value=[]
        result[name]={'status':status,'query':keyword,'items':value if valid else [],
                      'reason':state.get('reason','')}
    return result


def capture(pages, keyword, source):
    rows=[]
    for page in pages:
        if not page:continue
        rows.append({k:copy.deepcopy(page.get(k)) for k in
            ('url','title','fetch_status','failure_code','fetch_method','fetched_at','body_excerpt','body_sha256','body_truncated')})
    return {'version':VERSION,'query':keyword,'source':copy.deepcopy(source),'pages':rows}


def grams(text):
    terms=re.findall(r'[a-zA-Z0-9]{2,}|[一-龥ぁ-んァ-ヶー]{2,}',text.lower())
    return {t for t in terms if re.match(r'[a-z0-9]',t)} | {t[i:i+2] for t in terms for i in range(len(t)-1)}


def project(saved, keyword, needs=None):
    if not saved:return {'version':VERSION,'status':'not_captured','query':keyword,'excerpts':[],'pages':[]}
    if saved.get('version')!=VERSION or saved.get('query')!=keyword:
        raise ContentQualityError('競合本文のクエリ・形式が一致しません。')
    hints=keyword+' '+' '.join(n['question'] for n in (needs or {}).get('selected',[]))
    terms=grams(hints);excerpts=[];pages=[]
    for page_index,page in enumerate(saved['pages'],1):
        body=page.get('body_excerpt') or ''
        pages.append({k:copy.deepcopy(page.get(k)) for k in ('url','fetch_status','failure_code','body_truncated','fetched_at')})
        if page.get('fetch_status')!='success' or not body:continue
        chunks=[(start,body[start:start+600]) for start in range(0,len(body),600)]
        selected=sorted(chunks,key=lambda row:(-len(terms & grams(row[1])),row[0]))[:3]
        for start,text in sorted(selected):
            excerpts.append({'id':f'p{page_index}:{start}','url':page['url'],'title':page.get('title'),
                             'text':text,'start':start,'end':start+len(text),'scope':'fetched_body_excerpt'})
    return {'version':VERSION,'status':'available' if excerpts else 'not_available','query':keyword,
            'source_sha256':digest(json.dumps(saved,ensure_ascii=False,sort_keys=True)),
            'pages':pages,'excerpts':excerpts}


def validate_gaps(plan, context):
    if 'content_gaps' not in plan:return
    gaps=plan['content_gaps']
    if not isinstance(gaps,list):raise ContentQualityError('競合の回答確認の形式が不正です。')
    needs={n['id']:n for n in context.get('reader_needs',{}).get('selected',[])}
    refs={e['id']:e for e in context.get('competitor_context',{}).get('excerpts',[])}
    questions={q['id'] for q in plan['items']}
    if needs and {g.get('need_id') for g in gaps}!=set(needs):
        raise ContentQualityError('競合の回答確認と採用した読者の問いが一致しません。')
    if len({g.get('need_id') for g in gaps})!=len(gaps):raise ContentQualityError('競合の回答確認が重複しています。')
    for gap in gaps:
        if (gap.get('status') not in ('answered_in_excerpt','gap_candidate','not_assessed')
            or any(not isinstance(gap.get(k),str) or not gap[k].strip() for k in ('question','reason','opportunity'))
            or not isinstance(gap.get('research_ids'),list) or any(i not in questions for i in gap['research_ids'])):
            raise ContentQualityError('競合の回答確認の根拠・調査先が不正です。')
        evidence=gap.get('evidence')
        if not isinstance(evidence,list) or (gap['status']!='not_assessed' and not evidence):
            raise ContentQualityError('競合本文の未確認を回答不足として扱えません。')
        for ref in evidence:
            if (not isinstance(ref,dict) or ref.get('excerpt_id') not in refs or not isinstance(ref.get('quote'),str)
                or not ref['quote'].strip() or ref['quote'] not in refs[ref['excerpt_id']]['text']):
                raise ContentQualityError('競合の回答確認の引用が本文抜粋にありません。')
