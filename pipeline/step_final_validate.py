"""Fail a job when its final article violates protected structural requirements."""
from __future__ import annotations

import json

from .db import get_artifact, upsert_artifact
from .step_structure_guard import extract_h2_titles, validate_structure


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    del keyword, api_key
    article = get_artifact(job_id, "article")
    contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
    violations = validate_structure(article["content_text"], contract, outline=False)
    report = {
        "stage": "final_article",
        "valid": not violations,
        "violations": violations,
        "h2s": extract_h2_titles(article["content_text"], outline=False),
    }
    artifact = upsert_artifact(
        job_id=job_id,
        step="structure_validation_final",
        content_type="application/json",
        content_text=json.dumps(report, ensure_ascii=False),
        meta={"stage": "final_article", "valid": not violations},
    )
    if violations:
        raise ValueError(f"Final article violates content contract: {violations}")
    print("[final_validate] Final article satisfies the content contract")
    return artifact
