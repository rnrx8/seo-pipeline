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
    """Fetch a single URL and extract h2/h3 headings. Always returns a result dict."""
    url = item.get("link", "")
    title = item.get("title", "")
    base = {"url": url, "title": title, "headings": [], "heading_count": 0, "fetch_status": "failed"}
    if not url:
        return base
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        headings = [
            {"level": tag.name, "text": tag.get_text(strip=True)}
            for tag in soup.find_all(["h2", "h3"])
            if tag.get_text(strip=True)
        ]
        return {
            **base,
            "headings": headings,
            "heading_count": len(headings),
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
            "q": keyword,
            "api_key": os.environ["SERPAPI_KEY"],
            "hl": "ja",
            "gl": "jp",
            "num": "10",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    organic = data.get("organic_results", [])

    # related_searches: [{"query": "..."}] → ["..."]
    related = [
        r.get("query", "")
        for r in data.get("related_searches", [])
        if r.get("query")
    ]

    # people_also_ask: question + snippet のみ抽出
    paa = [
        {"question": r.get("question", ""), "snippet": r.get("snippet", "")}
        for r in data.get("people_also_ask", [])
        if r.get("question")
    ]

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
