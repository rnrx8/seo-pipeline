"""Bounded source discovery, separate from model inference and evidence fetching."""
import hashlib
import json
import os
import requests
from .content_quality import ContentQualityError
from .db import get_optional_artifact, upsert_artifact
from .serp_sources import canonical_url, _response_json
from .source_freshness import current_check_date
from . import quality_budget

SEARCH_TOOL = {
    'name':'search_sources',
    'description':'必要な資料のURLを検索する。本文確認ではない。取得済みURLで回答できない場合のみ使い、結果の要約を根拠にせずfetch_current_pageで本文を読む。1対象につき最大3検索。',
    'input_schema':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False},
}

def require_search_config():
    if not os.getenv('SERPER_API_KEY','').strip():
        raise ContentQualityError('上限付き資料検索にはSERPER_API_KEYが必要です。高額な内蔵検索へ自動切替しません。')

class SourceSearch:
    def __init__(self, job_id, scope, fresh):
        require_search_config()
        self.job_id,self.fresh=job_id,fresh
        self.step='source_search_'+hashlib.sha256((current_check_date()+scope).encode()).hexdigest()
        old=get_optional_artifact(job_id,self.step)
        self.records=json.loads(old['content_text']) if old else {}

    def save(self):
        upsert_artifact(job_id=self.job_id,step=self.step,content_type='application/json',
                        content_text=json.dumps(self.records,ensure_ascii=False),meta={})

    def __call__(self, query):
        if not isinstance(query,str) or not query.strip() or len(query)>300:
            return {'status':'failed','reason':'検索語は1〜300文字で指定してください。'}
        query=query.strip()
        if query in self.records:
            return self.records[query]  # includes failed/unknown requests; never auto-rebill
        if len(self.records)>=3:
            return {'status':'failed','reason':'この対象の検索上限に達しました。既知のURL本文を確認してください。'}
        # Reserve before sending; persist pending to prevent ambiguous-outcome retries.
        index=quality_budget.reserve_search(query)
        self.records[query]={'status':'failed','reason':'検索が中断されました。同じ要求を自動再送しません。'}
        self.save()
        try:
            response=requests.post('https://google.serper.dev/search',
                headers={'X-API-KEY':os.environ['SERPER_API_KEY']},
                json={'q':query,'gl':'jp','hl':'ja','num':10},timeout=(5,30))
            raw=_response_json(response,'Serper')
            if raw.get('error') or not isinstance(raw.get('organic'),list):
                raise ContentQualityError('検索結果の形式が不正です。')
            if (raw.get('searchParameters') or {}).get('q',query).strip()!=query:
                raise ContentQualityError('資料検索の検索語が一致しません。')
            results=[]
            for row in raw['organic'][:5]:
                if not isinstance(row,dict):continue
                url=row.get('link','')
                if not isinstance(url,str) or len(url)>2048:continue
                try:
                    canonical_url(url)
                    self.fresh.check_allowed(url)
                except (ValueError,RuntimeError):continue
                results.append({'url':url,'title':str(row.get('title',''))[:160],
                                'snippet':str(row.get('snippet',''))[:350]})
            value={'status':'success','query':query,'results':results,'evidence':False}
        except (requests.RequestException,ValueError,RuntimeError):
            # Never expose authenticated request headers or provider diagnostics.
            value={'status':'failed','reason':'資料検索を完了できませんでした。本文未確認のまま断定しないでください。'}
        self.records[query]=value
        self.save()
        # Fixed credit allowance retained even for failed requests (invoice unknown).
        quality_budget.settle_search(index,value['status'])
        return value
