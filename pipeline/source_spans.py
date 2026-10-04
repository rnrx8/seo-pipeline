"""Select immutable source spans instead of asking a model to copy quotations."""
import copy

SPAN_POLICY = '''
【今回の出典指定形式：quoteを文字列で返す指示より優先】
sources.excerptsに原文とsource_refがある。evidenceにはsource_refを返す。URLもシステムが対応する原文から取得する。
source_refは今回の要求内だけで有効なID。別要求のIDを流用しない。条件や文章が隣の抜粋へ続く場合は隣接抜粋も読み、必要なら複数IDを選ぶ。
主張を直接支える原文のIDを選ぶ。IDは内容を確認せず選んではいけない。
引用文はシステムが原文から取り出すため、quoteを自分で書き直さない。
専門家の氏名・資格・関与の根拠もexpert_source_refで同じURLの原文IDを指定する。不要なら空文字。
段落・表の前後や対象・列見出しは、同じページの隣接抜粋を読んで条件を確認する。
image_sourcesのimage_not_readは所在だけ把握した未読画像。altや画像URLを画像本文の引用・価格根拠として使わない。必要な情報が画像にある場合は本文未取得として扱う。
取得日時fetched_atは資料の取得日時であり、統計の集計時点や価格の適用開始日ではない。
'''


def indexed_sources(pages):
    result=[];index={}
    for page in pages:
        visible={k:v for k,v in page.items() if k!='text'}
        excerpts=[]
        # Never form a quotation across the adapter's omission boundary.
        for part in page.get('text','').split('\n[中略：取得本文の抜粋]\n'):
            for start in range(0,len(part),256):
                text=part[start:start+256]
                if not text.strip():continue
                # IDs are scoped to this complete request, whose fingerprint binds cached answers.
                key='s'+str(len(index)+1)
                ref={'url':page['url'],'quote':text}
                if key in index and index[key]!=ref:raise ValueError('Source reference collision')
                index[key]=ref
                excerpts.append({'source_ref':key,'text':text})
                if start+256>=len(part):break
        visible['excerpts']=excerpts;result.append(visible)
    return result,index


def span_schema(matrix_schema, question_ids=None):
    result=copy.deepcopy(matrix_schema)
    if question_ids is not None:
        ids=list(question_ids)
        if not ids or len(set(ids))!=len(ids):raise ValueError('Question IDs must be nonempty and unique')
        # Assigned below after converting evidence fields.
    evidence=result['format']['schema']['properties']['items']['items']['properties']['evidence']['items']
    evidence['properties'].pop('url')
    evidence['required'].remove('url')
    for original,replacement in [('quote','source_ref'),('expert_qualification_quote','expert_source_ref')]:
        evidence['properties'][replacement]=evidence['properties'].pop(original)
        evidence['required']=[replacement if key==original else key for key in evidence['required']]
    if question_ids is not None:
        row=result['format']['schema']['properties']['items']['items']
        row['properties'].pop('id')
        row['required'].remove('id')
        result['format']['schema']['properties']['items']={
            'type':'object','properties':{key:copy.deepcopy(row) for key in ids},
            'required':ids,'additionalProperties':False}
    return result


def expand_references(value,index):
    """Invalid IDs fail closed; source selection is not a semantic approval."""
    result=copy.deepcopy(value)
    if isinstance(result.get('items'),dict):
        result['items']=[{**row,'id':key} for key,row in result['items'].items()]
    for item in result['items']:
        bad=False
        for ref in item['evidence']:
            source=index.get(ref.pop('source_ref',''))
            expert_key=ref.pop('expert_source_ref','')
            expert=index.get(expert_key) if expert_key else None
            valid_source=source is not None
            ref['url']=source['url'] if source else ''
            valid_expert=not expert_key or (expert is not None and expert['url']==ref['url'])
            ref['quote']=source['quote'] if valid_source else ''
            ref['expert_qualification_quote']=expert['quote'] if expert and valid_expert else ''
            bad=bad or not valid_source or not valid_expert
        if bad:
            item.update(status='unresearched',verified=False,
                        reason='原文IDまたは対応URLが不正です。'+item.get('reason',''))
    return result
