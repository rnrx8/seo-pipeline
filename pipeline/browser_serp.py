"""Authenticate and bind a browser snapshot before the pipeline is scheduled."""
import json
import os

import requests

from .serp_sources import SerpQualityError, verified_serp


class BrowserAuthorizationError(ValueError):
    pass


def authenticate_owner(authorization: str | None, tenant_id: str | None) -> None:
    if not authorization or not authorization.startswith('Bearer ') or not tenant_id:
        raise BrowserAuthorizationError('ログインし直してください。')
    try:
        response = requests.get(
            os.environ['SUPABASE_URL'].rstrip('/') + '/auth/v1/user',
            headers={'apikey': os.environ['SUPABASE_KEY'], 'Authorization': authorization},
            timeout=(5, 10),
        )
        user = response.json() if response.ok else {}
    except (requests.RequestException, ValueError):
        raise BrowserAuthorizationError('ログインを確認できませんでした。再度お試しください。') from None
    if user.get('id') != tenant_id:
        raise BrowserAuthorizationError('この記事を実行する権限がありません。')


def bind_snapshot(snapshot: dict, job: dict, keyword: str) -> dict:
    if job.get('main_keyword') != keyword:
        raise SerpQualityError('記事と取得した検索キーワードが一致しません。')
    if len(json.dumps(snapshot, ensure_ascii=False)) > 50000:
        raise SerpQualityError('検索結果が大きすぎます。もう一度取得してください。')
    results = snapshot.get('organic_results')
    if not isinstance(results, list) or not 3 <= len(results) <= 10:
        raise SerpQualityError('自然検索結果を3〜10件取得してから実行してください。')
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or len(item['title']) > 1000 or len(item.get('link', '')) > 4096:
            raise SerpQualityError('検索結果の形式が不正です。')
    bound = {**snapshot, 'job_id': job['id']}
    # Do not trust caller-supplied provenance, questions, snippets or timestamp
    # beyond the explicitly validated browser snapshot fields.
    bound = {key: bound[key] for key in ['job_id', 'query', 'observed_at', 'search_url', 'organic_results'] if key in bound}
    verified_serp(bound, keyword, job['id'])
    return bound
