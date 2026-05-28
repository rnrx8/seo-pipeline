import time
from .db import insert_job, update_job_status, get_job
from . import step_serp, step_intent, step_fact_sheet, step_outline, step_service_map, step_article, step_cta_inject, step_review, step_fact_review

# Seconds to wait between Claude API steps to avoid rate limits (tokens/min)
STEP_DELAY = 15


def run_pipeline(keyword: str, job_id: str | None = None) -> str:
    """Run the full SEO article generation pipeline. Returns the job_id.

    If job_id is provided (Railway mode: job pre-created by Next.js), uses that job.
    Otherwise creates a new job (CLI mode).
    """
    print(f"\n=== SEO Pipeline: {keyword!r} ===\n")

    if job_id is None:
        job_id = insert_job(keyword)
        print(f"Job created: {job_id}\n")
    else:
        print(f"Using existing job: {job_id}\n")

    base_steps = [
        step_serp.run,
        step_intent.run,
        step_fact_sheet.run,
        step_outline.run,
    ]

    try:
        for step_fn in base_steps:
            step_fn(job_id, keyword)
            print(f"  (waiting {STEP_DELAY}s for rate limit...)")
            time.sleep(STEP_DELAY)

        # service_map: run when job has service_id or cta_id
        job = get_job(job_id)
        if job.get("service_id") or job.get("cta_id"):
            print("[pipeline] service_id/cta_id found → running service_map step")
            step_service_map.run(job_id, keyword)
            print(f"  (waiting {STEP_DELAY}s for rate limit...)")
            time.sleep(STEP_DELAY)

        step_article.run(job_id, keyword)
        print(f"  (waiting {STEP_DELAY}s for rate limit...)")
        time.sleep(STEP_DELAY)

        # cta_inject: ensure CTA is present at correct positions
        job = get_job(job_id)
        if job.get("cta_id"):
            print("[pipeline] cta_id found → running cta_inject step")
            step_cta_inject.run(job_id, keyword)
            print(f"  (waiting {STEP_DELAY}s for rate limit...)")
            time.sleep(STEP_DELAY)

        # 情報精度99.9%モード: article後にfact_reviewを追加実行
        if job.get("high_accuracy_mode"):
            print("[pipeline] high_accuracy_mode=True → running fact_review step")
            step_fact_review.run(job_id, keyword)
            print(f"  (waiting {STEP_DELAY}s for rate limit...)")
            time.sleep(STEP_DELAY)

        step_review.run(job_id, keyword)

        update_job_status(job_id, "done")
        print(f"\n=== Done (job_id={job_id}) ===\n")
    except Exception:
        update_job_status(job_id, "failed")
        raise

    return job_id
