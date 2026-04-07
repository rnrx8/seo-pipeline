import os
import requests


def _headers(extra: dict | None = None) -> dict:
    key = os.environ["SUPABASE_KEY"]
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"


def insert_job(keyword: str) -> str:
    """Insert a new job and return its id."""
    url = f"{_base()}/jobs"
    resp = requests.post(
        url,
        json={"main_keyword": keyword, "status": "queued"},
        headers=_headers({"Prefer": "return=representation"}),
    )
    resp.raise_for_status()
    return resp.json()[0]["id"]


def update_job_status(job_id: str, status: str) -> None:
    url = f"{_base()}/jobs"
    resp = requests.patch(
        url,
        json={"status": status},
        params={"id": f"eq.{job_id}"},
        headers=_headers(),
    )
    resp.raise_for_status()


def upsert_artifact(
    job_id: str,
    step: str,
    content_type: str,
    content_text: str,
    content: dict | None = None,
    payload: dict | None = None,
    meta: dict | None = None,
) -> dict:
    """Upsert an artifact row and return the saved record."""
    url = f"{_base()}/artifacts"
    data: dict = {
        "job_id": job_id,
        "step": step,
        "content_type": content_type,
        "content_text": content_text,
    }
    if content is not None:
        data["content"] = content
    if payload is not None:
        data["payload"] = payload
    if meta is not None:
        data["meta"] = meta

    resp = requests.post(
        url,
        json=data,
        params={"on_conflict": "job_id,step"},
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
    )
    resp.raise_for_status()
    return resp.json()[0]


def get_job(job_id: str) -> dict:
    """Fetch a single job by id. Raises if not found."""
    url = f"{_base()}/jobs"
    resp = requests.get(
        url,
        params={"id": f"eq.{job_id}", "limit": "1"},
        headers=_headers(),
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(f"Job not found: job_id={job_id}")
    return rows[0]


def get_company_settings(user_id: str, category: str) -> list:
    """カテゴリに一致する企業設定を取得（レベル降順）"""
    resp = requests.get(
        f"{_base()}/company_settings",
        headers=_headers(),
        params={
            "tenant_id": f"eq.{user_id}",
            "category": f"eq.{category}",
            "order": "recommend_level.desc",
            "select": "*",
        },
    )
    return resp.json() if resp.status_code == 200 else []


def get_primary_sources(user_id: str, category: str) -> list:
    """カテゴリに一致する一次情報を取得"""
    resp = requests.get(
        f"{_base()}/primary_sources",
        headers=_headers(),
        params={
            "tenant_id": f"eq.{user_id}",
            "category": f"eq.{category}",
            "select": "title,content_text",
        },
    )
    return resp.json() if resp.status_code == 200 else []


def get_job_company_restriction(job_id: str) -> str:
    """jobsテーブルからcompany_restrictionを取得"""
    response = requests.get(
        f"{_base()}/jobs",
        headers=_headers(),
        params={
            "id": f"eq.{job_id}",
            "select": "company_restriction",
        },
    )
    if response.status_code == 200 and response.json():
        return response.json()[0].get("company_restriction", "ai")
    return "ai"


def get_artifact(job_id: str, step: str) -> dict:
    """Fetch a single artifact by job_id and step. Raises if not found."""
    url = f"{_base()}/artifacts"
    resp = requests.get(
        url,
        params={"job_id": f"eq.{job_id}", "step": f"eq.{step}", "limit": "1"},
        headers=_headers(),
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(f"Artifact not found: job_id={job_id}, step={step}")
    return rows[0]
