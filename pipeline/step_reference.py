"""Fetch user-requested structure reference pages and persist a compact outline."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .db import get_job, upsert_artifact


MAX_REFERENCE_URLS = 3
MAX_RESPONSE_BYTES = 1_500_000
REFERENCE_TERMS = ("参考", "リファレンス", "構成", "見本", "踏襲")


def extract_reference_urls(custom_prompt: str | None) -> list[str]:
    if not custom_prompt or not any(term in custom_prompt for term in REFERENCE_TERMS):
        return []
    urls = re.findall(r"https://[^\s<>()\]）]+", custom_prompt)
    result: list[str] = []
    for url in urls:
        cleaned = url.rstrip("。、,.;:！!？?")
        if cleaned not in result:
            result.append(cleaned)
    return result[:MAX_REFERENCE_URLS]


def _is_public_https_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified)):
            return False
    return True


def _get_with_safe_redirects(url: str, max_redirects: int = 3) -> requests.Response:
    current = url
    for _ in range(max_redirects + 1):
        if not _is_public_https_url(current):
            raise ValueError("reference URL must resolve to a public HTTPS address")
        response = requests.get(
            current,
            headers={"User-Agent": "SEO-Pipeline-ReferenceFetcher/1.0"},
            timeout=(5, 15),
            allow_redirects=False,
            stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise ValueError("redirect response did not include Location")
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        return response
    raise ValueError("too many redirects while fetching reference URL")


def fetch_reference(url: str) -> dict:
    response = _get_with_safe_redirects(url)
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        response.close()
        raise ValueError(f"unsupported reference content type: {content_type or 'unknown'}")

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            response.close()
            raise ValueError("reference page exceeded size limit")
        chunks.append(chunk)
    final_url = response.url
    encoding = response.encoding or "utf-8"
    response.close()

    html = b"".join(chunks).decode(encoding, errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    headings = [
        {"level": int(tag.name[1]), "text": " ".join(tag.get_text(" ", strip=True).split())}
        for tag in root.find_all(re.compile(r"^h[1-4]$"))
        if tag.get_text(" ", strip=True)
    ]
    text = "\n".join(line.strip() for line in root.get_text("\n").splitlines() if line.strip())
    return {
        "url": url,
        "final_url": final_url,
        "title": " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split()),
        "headings": headings[:120],
        "excerpt": text[:12000],
    }


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict | None:
    del keyword, api_key
    job = get_job(job_id)
    urls = extract_reference_urls(job.get("custom_prompt"))
    if not urls:
        print("[reference] No structure reference URL configured — skipping")
        return None

    pages: list[dict] = []
    errors: list[dict] = []
    for url in urls:
        try:
            pages.append(fetch_reference(url))
            print(f"[reference] Fetched: {url}")
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)[:300]})
            print(f"[reference] Warning: could not fetch {url}: {exc}")

    payload = {
        "urls": urls,
        "fetched_count": len(pages),
        "pages": pages,
        "errors": errors,
    }
    artifact = upsert_artifact(
        job_id=job_id,
        step="reference_structure",
        content_type="application/json",
        content_text=json.dumps(payload, ensure_ascii=False),
        meta={"requested_count": len(urls), "fetched_count": len(pages)},
    )
    print(f"[reference] Done ({len(pages)}/{len(urls)} fetched) → artifact id={artifact['id']}")
    return artifact
