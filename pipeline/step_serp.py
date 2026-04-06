import json
import os
import requests
from .db import upsert_artifact


SERPAPI_ENDPOINT = "https://serpapi.com/search"


def run(job_id: str, keyword: str) -> dict:
    """Search Google via SerpApi and store raw results."""
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

    # related_searches: [{"query": "..."}] → ["..."] に平坦化
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

    structured = {
        "organic_results": [
            {
                "title":   r.get("title", ""),
                "link":    r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in organic
        ],
        "related_searches": related,
        "people_also_ask":  paa,
    }

    artifact = upsert_artifact(
        job_id=job_id,
        step="serp",
        content_type="application/json",
        content_text=json.dumps(structured, ensure_ascii=False),
        payload=data,
    )
    print(
        f"[serp] Saved {len(organic)} organic / {len(paa)} PAA / {len(related)} related"
        f" → artifact id={artifact['id']}"
    )
    return artifact
