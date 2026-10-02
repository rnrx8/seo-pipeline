"""Select immutable source spans instead of asking a model to copy quotations."""
import copy
import hashlib

SPAN_POLICY = '''
【今回の出典指定形式：quoteを文字列で返す指示より優先】
sources.excerptsに原文とsource_refがある。evidenceにはsource_refを返す。URLもシステムが対応する原文から取得する。
主張を直接支える原文のIDを選ぶ。IDは内容を確認せず選んではいけない。
引用文はシステムが原文から取り出すため、quoteを自分で書き直さない。
専門家の氏名・資格・関与の根拠もexpert_source_refで同じURLの原文IDを指定する。不要なら空文字。
段落・表の前後や対象・列見出しは、同じページの隣接抜粋を読んで条件を確認する。
取得日時fetched_atは資料の取得日時であり、統計の集計時点や価格の適用開始日ではない。
'''


def indexed_sources(pages):
    result=[];index={}
    for page in pages:
        visible={k:v for k,v in page.items() if k!='text'}
        excerpts=[]
        # Never form a quotation across the adapter's omission boundary.
        for part in page.get('text','').split('\n[中略：取得本文の抜粋]\n'):
            for start in range(0,len(part),192):
                text=part[start:start+256]
                if not text.strip():continue
                key='src-'+hashlib.sha256((page['url']+'\0'+text).encode()).hexdigest()[:24]
                ref={'url':page['url'],'quote':text}
                if key in index and index[key]!=ref:raise ValueError('Source reference collision')
                index[key]=ref
                excerpts.append({'source_ref':key,'text':text})
                if start+256>=len(part):break
        visible['excerpts']=excerpts;result.append(visible)
    return result,index


def span_schema(matrix_schema):
    result=copy.deepcopy(matrix_schema)
    evidence=result['format']['schema']['properties']['items']['items']['properties']['evidence']['items']
    evidence['properties'].pop('url')
    evidence['required'].remove('url')
    for original,replacement in [('quote','source_ref'),('expert_qualification_quote','expert_source_ref')]:
        evidence['properties'][replacement]=evidence['properties'].pop(original)
        evidence['required']=[replacement if key==original else key for key in evidence['required']]
    return result


def expand_references(value,index):
    """Invalid IDs fail closed; source selection is not a semantic approval."""
    result=copy.deepcopy(value)
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
