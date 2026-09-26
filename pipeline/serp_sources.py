"""Search providers and validation; never substitute a different query's SERP."""
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

TIMEOUT = (5, 45)


class SerpQualityError(RuntimeError):
    """A response cannot safely be used as the requested competitive SERP."""


class SerpConfigurationError(RuntimeError):
    pass


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host or parts.username or parts.password:
        raise SerpQualityError("競合情報に無効なURLが含まれています。")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SerpQualityError("競合情報に公開サイト以外のURLが含まれています。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise SerpQualityError("競合情報に公開サイト以外のURLが含まれています。")
    return urlunsplit(("https", host.removeprefix("www."), parts.path.rstrip("/"), parts.query, ""))


def validate_results(data: dict, keyword: str) -> list[dict]:
    if not isinstance(data, dict) or data.get("error"):
        raise SerpQualityError("検索サービスが有効な競合情報を返しませんでした。再取得が必要です。")
    query = data.get("search_parameters", {}).get("q")
    if query is not None and query.strip() != keyword.strip():
        raise SerpQualityError("検索サービスの検索語が記事のキーワードと一致しません。")
    results = data.get("organic_results")
    if not isinstance(results, list) or not results:
        raise SerpQualityError("自然検索結果が空のため、記事生成を停止しました。")
    seen = set()
    normalized = []
    for result in results[:10]:
        if not isinstance(result, dict) or not result.get("title") or not isinstance(result.get("link"), str):
            raise SerpQualityError("競合情報の形式が不正なため、記事生成を停止しました。")
        key = canonical_url(result["link"])
        if key in seen:
            raise SerpQualityError("同じ競合URLが重複しているため、再取得が必要です。")
        seen.add(key)
        normalized.append({**result, "position": len(normalized) + 1})
    # Some upstream failures preserve query_displayed but change pagination q.
    for section in ("pagination", "serpapi_pagination"):
        for value in (data.get(section) or {}).values():
            for link in (value.values() if isinstance(value, dict) else [value]):
                if isinstance(link, str) and link.startswith("http"):
                    query_values = parse_qs(urlsplit(link).query).get("q", [])
                    if query_values and query_values[0].strip() != keyword.strip():
                        raise SerpQualityError("検索サービスが検索語を変更したため、記事生成を停止しました。")
    return normalized


def _response_json(response, provider: str) -> dict:
    # Do not expose request URLs: SerpAPI authenticates in the query string.
    if not response.ok:
        raise SerpQualityError(f"{provider}の競合情報取得に失敗しました（HTTP {response.status_code}）。")
    try:
        data = response.json()
    except ValueError:
        raise SerpQualityError(f"{provider}の応答を読み取れませんでした。") from None
    if not isinstance(data, dict):
        raise SerpQualityError(f"{provider}の応答形式が不正です。")
    return data


def _serpapi(keyword: str, *, verbatim: bool = False) -> dict:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise SerpConfigurationError("SERPAPI_KEYが設定されていません。")
    params = dict(engine="google", q=keyword, api_key=key, hl="ja", gl="jp",
                  google_domain="google.co.jp", device="desktop", no_cache="true")
    if verbatim:
        params["tbs"] = "li:1"
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=TIMEOUT)
    except requests.RequestException:
        raise SerpQualityError("SerpAPIへの接続に失敗しました。時間をおいて再取得してください。") from None
    return _response_json(response, "SerpAPI")


def fetch_serp(keyword: str) -> tuple[dict, dict]:
    provider = os.getenv("SERP_PROVIDER", "").strip().lower() or (
        "serper" if os.getenv("SERPER_API_KEY") else "serpapi"
    )
    fetched_at = datetime.now(timezone.utc).isoformat()
    source = {"provider": provider, "query": keyword, "fetched_at": fetched_at,
              "country": "jp", "language": "ja", "search_mode": "standard"}
    if provider == "serper":
        key = os.getenv("SERPER_API_KEY")
        if not key:
            raise SerpConfigurationError("SERPER_API_KEYが設定されていません。")
        try:
            response = requests.post("https://google.serper.dev/search",
                                     headers={"X-API-KEY": key},
                                     json={"q": keyword, "gl": "jp", "hl": "ja", "num": 10},
                                     timeout=TIMEOUT)
        except requests.RequestException:
            raise SerpQualityError("Serperへの接続に失敗しました。時間をおいて再取得してください。") from None
        raw = _response_json(response, "Serper")
        data = {"organic_results": raw.get("organic", []),
                "related_questions": raw.get("peopleAlsoAsk", []),
                "related_searches": raw.get("relatedSearches", []),
                "search_parameters": {"q": raw.get("searchParameters", {}).get("q", keyword)},
                "raw_response": raw}
        if raw.get("error") or raw.get("message") and not raw.get("organic"):
            raise SerpQualityError("Serperから有効な競合情報を取得できませんでした。")
        validate_results(data, keyword)
        source["validation"] = "response_checked"
        return data, source
    if provider != "serpapi":
        raise SerpConfigurationError("SERP_PROVIDERにはserperまたはserpapiを指定してください。")
    data = _serpapi(keyword)
    organic = validate_results(data, keyword)
    # Diagnostic only: verbatim results must NEVER become normal Google rankings.
    probe = _serpapi(keyword, verbatim=True)
    other = validate_results(probe, keyword)
    first_urls = {canonical_url(r["link"]) for r in organic}
    probe_urls = {canonical_url(r["link"]) for r in other}
    overlap = len(first_urls & probe_urls)
    source["consistency_check"] = {"overlap": overlap, "standard_count": len(organic),
                                   "probe_count": len(other), "probe_mode": "verbatim"}
    source["search_id"] = data.get("search_metadata", {}).get("id")
    if min(len(first_urls), len(probe_urls)) >= 3 and overlap / min(len(first_urls), len(probe_urls)) < 0.2:
        raise SerpQualityError(
            "検索結果の不整合を検出したため、記事生成を停止しました。"
            "Serperへの切り替え、またはブラウザで確認した競合情報の登録が必要です。"
        )
    source["validation"] = "consistency_checked"
    return data, source


def verified_serp(snapshot: dict, keyword: str, job_id: str) -> tuple[dict, dict]:
    """Operator-recorded, job-scoped browser observation; expires after 24 hours."""
    if snapshot.get("job_id") != job_id or snapshot.get("query") != keyword:
        raise SerpQualityError("確認済み競合情報の記事・キーワードが一致しません。")
    try:
        observed = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - observed
    except (KeyError, ValueError, TypeError):
        raise SerpQualityError("確認済み競合情報の取得日時が無効です。") from None
    if age < timedelta(minutes=-5) or age > timedelta(hours=24):
        raise SerpQualityError("確認済み競合情報の有効期限が切れました。ブラウザで再確認してください。")
    search_url = snapshot.get("search_url", "")
    parsed = urlsplit(search_url)
    params = parse_qs(parsed.query)
    if parsed.scheme != "https" or parsed.hostname not in {"www.google.co.jp", "www.google.com"} or params.get("q") != [keyword] or any(k in params for k in ("tbs", "tbm", "udm")):
        raise SerpQualityError("確認済み競合情報には通常のGoogle検索画面のURLが必要です。")
    data = {"organic_results": snapshot.get("organic_results", []),
            "related_questions": snapshot.get("people_also_ask", []),
            "related_searches": snapshot.get("related_searches", []),
            "search_parameters": {"q": keyword}}
    validate_results(data, keyword)
    source = {"provider": "browser_verified", "query": keyword, "fetched_at": observed.isoformat(),
              "search_mode": "standard", "validation": "browser_verified", "search_url": search_url}
    return data, source
