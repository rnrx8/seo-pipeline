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

    # Build a compact text summary of organic results
    organic = data.get("organic_results", [])
    lines = []
    for i, r in enumerate(organic, 1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        lines.append(f"{i}. {title}\n   URL: {link}\n   {snippet}")
    content_text = "\n\n".join(lines)

    artifact = upsert_artifact(
        job_id=job_id,
        step="serp",
        content_type="application/json",
        content_text=content_text,
        payload=data,
    )
    print(f"[serp] Saved {len(organic)} results → artifact id={artifact['id']}")
    return artifact
