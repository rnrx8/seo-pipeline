"""FastAPI wrapper for the SEO pipeline."""
import asyncio
import io
import os
import time
from contextlib import asynccontextmanager, suppress

import pypdf
import requests as req
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from pipeline import (  # noqa: E402  (after load_dotenv)
    step_article,
    step_fact_sheet,
    step_intent,
    step_outline,
    step_review,
    step_serp,
)
from pipeline.db import get_bug_fixing_jobs, get_job, get_stale_queued_jobs, get_user_credits_total, get_user_email, insert_job, update_job_error, update_job_status, update_job_step  # noqa: E402
from pipeline.notify import alert_failed_job, alert_stale_job  # noqa: E402

STEP_DELAY = 15  # seconds between steps (same as runner.py)
MAX_AUTO_RETRIES = 2
WATCHDOG_INTERVAL_SECONDS = 300   # 5分ごとにチェック
STALE_THRESHOLD_MINUTES = 5       # 5分以上 queued なら再実行対象

_notified_stale: set[str] = set()  # 通知済み job_id（再起動でリセット、重複防止用）


async def _watchdog() -> None:
    """起動60秒後から5分ごとに queued 滞留ジョブを検出して再実行・通知する。"""
    await asyncio.sleep(60)
    while True:
        try:
            stale = get_stale_queued_jobs(STALE_THRESHOLD_MINUTES)
            for job in stale:
                job_id = job["id"]
                keyword = job.get("main_keyword", "")
                loop = asyncio.get_running_loop()

                print(f"[watchdog] Re-triggering stale job: {job_id} ({keyword!r})")
                loop.run_in_executor(None, _run_pipeline, job_id, keyword)

                if job_id not in _notified_stale:
                    _notified_stale.add(job_id)
                    tenant_id = job.get("tenant_id", "")
                    user_email = get_user_email(tenant_id)
                    loop.run_in_executor(
                        None, alert_stale_job, job_id, keyword, user_email, STALE_THRESHOLD_MINUTES
                    )
                    print(f"[watchdog] Alert sent for stale job: {job_id}")
        except Exception as exc:
            print(f"[watchdog] Error: {exc}")
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


async def _requeue_bug_fixing_jobs() -> None:
    """起動時に bug_fixing ジョブを再キューする（修正デプロイ後の自動再実行）。"""
    await asyncio.sleep(10)  # DB接続が安定するまで待機
    try:
        jobs = get_bug_fixing_jobs()
        if not jobs:
            return
        print(f"[startup] Found {len(jobs)} bug_fixing job(s) — re-queuing...")
        loop = asyncio.get_running_loop()
        for job in jobs:
            job_id = job["id"]
            keyword = job.get("main_keyword", "")
            update_job_status(job_id, "queued")
            loop.run_in_executor(None, _run_pipeline, job_id, keyword)
            print(f"[startup] Re-queued: {job_id} ({keyword!r})")
    except Exception as exc:
        print(f"[startup] Failed to requeue bug_fixing jobs: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_requeue_bug_fixing_jobs())
    task = asyncio.create_task(_watchdog())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="SEO Pipeline API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


# ---------- Request / Response models ----------

class GenerateRequest(BaseModel):
    keyword: str
    job_id: str | None = None


class GenerateResponse(BaseModel):
    job_id: str
    status: str


# ---------- Background task ----------

def _resolve_api_key(job: dict) -> str | None:
    """Return the Anthropic API key to use based on whether the user is paid."""
    tenant_id = job.get("tenant_id")
    if tenant_id:
        try:
            credits_total = get_user_credits_total(tenant_id)
            if credits_total > 5:
                paid_key = os.getenv("ANTHROPIC_API_KEY_PAID")
                if paid_key:
                    print("[pipeline] Using paid Anthropic API key")
                    return paid_key
        except Exception as e:
            print(f"[pipeline] Warning: could not check user plan: {e}")
    return None


def _run_pipeline(job_id: str, keyword: str, _retry: int = 0) -> None:
    """Run pipeline steps for an existing job, respecting delivery_type."""
    try:
        job = get_job(job_id)
    except Exception:
        job = {}
    delivery_type = job.get("delivery_type") or "full"
    api_key = _resolve_api_key(job)

    STEP_KEYS = {
        step_serp.run:       "serp",
        step_intent.run:     "search_intent",
        step_fact_sheet.run: "fact_sheet",
        step_outline.run:    "outline",
        step_article.run:    "article",
        step_review.run:     "review",
    }

    if delivery_type == "research_only":
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run]
    elif delivery_type == "outline_only":
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run, step_outline.run]
    else:
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run, step_outline.run, step_article.run, step_review.run]

    print(f"[pipeline] delivery_type={delivery_type}, steps={len(steps)}")
    failed_step = None
    try:
        update_job_status(job_id, "running")
        for i, step_fn in enumerate(steps):
            failed_step = STEP_KEYS[step_fn]
            update_job_step(job_id, failed_step)
            step_fn(job_id, keyword, api_key=api_key)
            if i < len(steps) - 1:
                print(f"  (waiting {STEP_DELAY}s for rate limit...)")
                time.sleep(STEP_DELAY)
        update_job_step(job_id, None)
        update_job_status(job_id, "done")
        print(f"\n=== Done (job_id={job_id}) ===\n")
    except Exception as exc:
        from pipeline.autofix import classify_error, create_github_issue
        error_info = classify_error(exc)

        # 一時的なエラーは自動リトライ
        if error_info["retryable"] and _retry < MAX_AUTO_RETRIES:
            delay = 30 * (2 ** _retry)  # 30s → 60s
            print(f"[pipeline] Auto-retry ({_retry + 1}/{MAX_AUTO_RETRIES}) in {delay}s: {error_info['reason']}")
            time.sleep(delay)
            update_job_step(job_id, None)
            update_job_status(job_id, "queued")
            _run_pipeline(job_id, keyword, _retry=_retry + 1)
            return

        update_job_step(job_id, None)
        # コードバグは修正デプロイ後に自動再実行するため bug_fixing ステータスにする
        new_status = "bug_fixing" if error_info["type"] == "code_bug" else "failed"
        update_job_status(job_id, new_status)
        try:
            update_job_error(job_id, str(exc))
        except Exception:
            pass
        print(f"[pipeline] ERROR job_id={job_id}: {exc} → status={new_status}")

        # コードバグは GitHub Issue を自動作成して auto-fix ワークフローへ
        if error_info["type"] == "code_bug":
            try:
                create_github_issue(job_id, keyword, str(exc), failed_step, error_info)
            except Exception as e:
                print(f"[pipeline] autofix issue creation failed: {e}")

        try:
            user_email = get_user_email(job.get("tenant_id", ""))
            alert_failed_job(job_id, keyword, user_email, error_msg=str(exc), failed_step=failed_step)
        except Exception:
            pass
        raise


# ---------- Endpoints ----------

@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    if req.job_id:
        job_id = req.job_id
    else:
        job_id = insert_job(req.keyword)

    background_tasks.add_task(_run_pipeline, job_id, req.keyword)
    return GenerateResponse(job_id=job_id, status="started")


@app.post("/extract-pdf")
async def extract_pdf(body: dict):
    file_path = body.get("file_path")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")

    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")

        response = req.get(
            f"{supabase_url}/storage/v1/object/documents/{file_path}",
            headers={"Authorization": f"Bearer {supabase_key}"},
        )

        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="File not found")

        pdf_file = io.BytesIO(response.content)
        reader = pypdf.PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"

        return {"text": text.strip()}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/service-status")
async def service_status():
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    try:
        r = req.get(
            "https://serpapi.com/account",
            params={"api_key": serpapi_key},
            timeout=5,
        )
        d = r.json() if r.status_code == 200 else {}
        serpapi = {
            "searches_left":      d.get("plan_searches_left"),
            "searches_per_month": d.get("searches_per_month"),
            "this_month_usage":   d.get("this_month_usage"),
            "plan_name":          d.get("plan_id"),
            "extra_credits":      d.get("extra_credits", 0),
        }
    except Exception as e:
        serpapi = {"error": str(e)}

    return {"serpapi": serpapi}


@app.get("/health")
async def health():
    return {"status": "ok"}
