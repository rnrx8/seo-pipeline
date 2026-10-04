"""Convert search-intent artifacts and job settings into a structural contract."""
from __future__ import annotations

import json

from .content_contract import build_content_contract
from .content_quality import confirmed_facts
from .db import get_artifact, get_job, get_service_by_id, upsert_artifact


def _optional_artifact(job_id: str, step: str) -> dict | None:
    try:
        return get_artifact(job_id, step)
    except Exception:
        return None


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    del api_key
    print("[content_contract] Building job-specific structure requirements...")
    job = get_job(job_id)
    intent = get_artifact(job_id, "search_intent")
    fact = get_artifact(job_id, "fact_sheet")
    attrs = _optional_artifact(job_id, "query_attrs")
    reference_artifact = _optional_artifact(job_id, "reference_structure")

    service = None
    if job.get("service_id"):
        service = get_service_by_id(job["service_id"])

    reference = {}
    if reference_artifact:
        try:
            reference = json.loads(reference_artifact.get("content_text") or "{}")
        except json.JSONDecodeError:
            reference = {}

    candidate_services = None
    if fact.get('meta',{}).get('canonical_research_answers'):
        from .research_requirements import require_matrix,load_plan
        require_matrix(job_id)
        candidate_services = load_plan(job_id)['candidate_services']

    contract = build_content_contract(
        keyword=keyword,
        intent_text=intent["content_text"],
        query_attrs_text=(attrs or {}).get("content_text"),
        fact_text=confirmed_facts(fact["content_text"]),
        job=job,
        service=service,
        reference_structure=reference,
        candidate_services=candidate_services,
    )
    artifact = upsert_artifact(
        job_id=job_id,
        step="content_contract",
        content_type="application/json",
        content_text=json.dumps(contract, ensure_ascii=False),
        meta={
            "version": contract["version"],
            "required_section_count": len(contract["required_sections"]),
            "service_treatment": contract["service_treatment"],
        },
    )
    keys = [section["key"] for section in contract["required_sections"]]
    print(f"[content_contract] Required={keys} → artifact id={artifact['id']}")
    return artifact
