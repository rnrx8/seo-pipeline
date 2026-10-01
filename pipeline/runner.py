import time
from .db import insert_job, update_job_status, get_job
from .step_plan import build_step_plan, requires_rate_limit_delay
from .ai import validate_model_credentials

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

    try:
        job = get_job(job_id)
        validate_model_credentials(job)
        steps = build_step_plan(job)
        print(f"[pipeline] delivery_type={job.get('delivery_type') or 'full'}, steps={len(steps)}")
        for i, (step_key, step_fn) in enumerate(steps):
            print(f"[pipeline] Running {step_key}")
            step_fn(job_id, keyword)
            if i < len(steps) - 1 and requires_rate_limit_delay(step_key):
                print(f"  (waiting {STEP_DELAY}s for rate limit...)")
                time.sleep(STEP_DELAY)

        update_job_status(job_id, "done")
        print(f"\n=== Done (job_id={job_id}) ===\n")
    except Exception:
        update_job_status(job_id, "failed")
        raise

    return job_id
