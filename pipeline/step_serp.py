import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from .db import upsert_artifact


SERPAPI_ENDPOINT = "https://serpapi.com/search"
FETCH_TIMEOUT = 5  # seconds per URL
MAX_WORKERS = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch_headings(item: dict) -> dict:
    """Fetch a single URL and extract h2/h3 headings and character count."""
    url = item.get("link", "")
    title = item.get("title", "")
    base = {"url": url, "title": title, "headings": [], "heading_count": 0, "word_count": 0, "fetch_status": "failed"}
    if not url:
        return base
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        headings = [
            {"level": tag.name, "text": tag.get_text(strip=True)}
            for tag in soup.find_all(["h2", "h3"])
            if tag.get_text(strip=True)
        ]
        body = soup.find("body")
        body_text = body.get_text() if body else soup.get_text()
        char_count = len([c for c in body_text if not c.isspace()])
        return {
            **base,
            "headings": headings,
            "heading_count": len(headings),
            "word_count": char_count,
            "fetch_status": "success",
        }
    except Exception:
        return base


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Search Google via SerpApi, fetch competitor headings, and store results."""
    print(f"[serp] Searching: {keyword!r}")

    resp = requests.get(
        SERPAPI_ENDPOINT,
        params={
            "engine": "google",
            "q": keyword,
            "api_key": os.environ["SERPAPI_KEY"],
            "hl": "ja",
            "gl": "jp",
            "google_domain": "google.co.jp",
            "num": "10",
            "no_cache": "true",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    print(f"[serp] API response keys: {list(data.keys())}")

    organic = data.get("organic_results", [])

    # related_searches: [{"query": "..."}] → ["..."]
    related = [
        r.get("query", "")
        for r in data.get("related_searches", [])
        if r.get("query")
    ]

    # people_also_ask / related_questions: question + snippet のみ抽出
    paa_raw = data.get("people_also_ask") or data.get("related_questions") or []
    print(f"[serp] PAA raw count from API: {len(paa_raw)}")
    paa = [
        {"question": r.get("question", ""), "snippet": r.get("snippet", "")}
        for r in paa_raw
        if r.get("question")
    ]

    # PAA が空の場合、疑問文クエリで追加検索して補完
    if not paa:
        print("[serp] No PAA found, trying question-focused query...")
        try:
            resp2 = requests.get(
                SERPAPI_ENDPOINT,
                params={
                    "engine": "google",
                    "q": f"{keyword} とは",
                    "api_key": os.environ["SERPAPI_KEY"],
                    "hl": "ja",
                    "gl": "jp",
                    "google_domain": "google.co.jp",
                    "no_cache": "true",
                },
            )
            if resp2.ok:
                data2 = resp2.json()
                paa_raw2 = data2.get("people_also_ask") or data2.get("related_questions") or []
                print(f"[serp] PAA from question query: {len(paa_raw2)}")
                paa = [
                    {"question": r.get("question", ""), "snippet": r.get("snippet", "")}
                    for r in paa_raw2
                    if r.get("question")
                ]
        except Exception as e:
            print(f"[serp] Question-focused query failed: {e}")

    # 競合サイトの見出しを並列取得（上位10件）
    print(f"[serp] Fetching headings for {len(organic)} URLs...")
    competitor_headings = [None] * len(organic)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_fetch_headings, item): i
            for i, item in enumerate(organic[:10])
        }
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            competitor_headings[i] = future.result()

    success = sum(1 for h in competitor_headings if h and h["fetch_status"] == "success")
    print(f"[serp] Headings fetched: {success}/{len(organic)} succeeded")

    structured = {
        "organic_results": [
            {
                "title":   r.get("title", ""),
                "link":    r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in organic
        ],
        "related_searches":    related,
        "people_also_ask":     paa,
        "competitor_headings": competitor_headings,
    }

    artifact = upsert_artifact(
        job_id=job_id,
        step="serp",
        content_type="application/json",
        content_text=json.dumps(structured, ensure_ascii=False),
        payload=data,
    )
    print(
        f"[serp] Saved {len(organic)} organic / {len(paa)} PAA / "
        f"{len(related)} related / {success} headings"
        f" → artifact id={artifact['id']}"
    )
    return artifact
