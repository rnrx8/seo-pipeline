import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from .db import get_optional_artifact, upsert_artifact
from .serp_sources import fetch_serp, validate_results, verified_serp
from .public_fetch import get_public_page


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
        resp = get_public_page(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
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
    """Acquire a validated SERP, fetch competitor headings, and store provenance."""
    print(f"[serp] Searching: {keyword!r}")

    override = get_optional_artifact(job_id, "serp_verified")
    if override:
        data, source = verified_serp(json.loads(override["content_text"]), keyword, job_id)
    else:
        data, source = fetch_serp(keyword)
    organic = validate_results(data, keyword)

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

    # Preserve missing PAA: a different query's questions are not this SERP.

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
        "source": source,
        "organic_results": [
            {
                "position": r["position"],
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
        meta={"source": source},
    )
    print(
        f"[serp] Saved {len(organic)} organic / {len(paa)} PAA / "
        f"{len(related)} related / {success} headings"
        f" → artifact id={artifact['id']}"
    )
    return artifact
