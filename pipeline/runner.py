import time
from .db import insert_job, update_job_status
from . import step_serp, step_intent, step_fact_sheet, step_outline, step_article, step_review

# Seconds to wait between Claude API steps to avoid rate limits (tokens/min)
STEP_DELAY = 15


def run_pipeline(keyword: str) -> str:
    """Run the full SEO article generation pipeline. Returns the job_id."""
    print(f"\n=== SEO Pipeline: {keyword!r} ===\n")

    job_id = insert_job(keyword)
    print(f"Job created: {job_id}\n")

    steps = [
        step_serp.run,
        step_intent.run,
        step_fact_sheet.run,
        step_outline.run,
        step_article.run,
        step_review.run,
    ]

    try:
        for i, step_fn in enumerate(steps):
            step_fn(job_id, keyword)
            if i < len(steps) - 1:
                print(f"  (waiting {STEP_DELAY}s for rate limit...)")
                time.sleep(STEP_DELAY)
        update_job_status(job_id, "done")
        print(f"\n=== Done (job_id={job_id}) ===\n")
    except Exception:
        update_job_status(job_id, "failed")
        raise

    return job_id
