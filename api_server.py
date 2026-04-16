"""FastAPI wrapper for the SEO pipeline."""
import io
import os
import time
from contextlib import asynccontextmanager

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
from pipeline.db import get_job, insert_job, update_job_status  # noqa: E402

STEP_DELAY = 15  # seconds between steps (same as runner.py)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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

def _run_pipeline(job_id: str, keyword: str) -> None:
    """Run pipeline steps for an existing job, respecting delivery_type."""
    try:
        job = get_job(job_id)
    except Exception:
        job = {}
    delivery_type = job.get("delivery_type") or "full"

    if delivery_type == "research_only":
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run]
    elif delivery_type == "outline_only":
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run, step_outline.run]
    else:
        steps = [step_serp.run, step_intent.run, step_fact_sheet.run, step_outline.run, step_article.run, step_review.run]

    print(f"[pipeline] delivery_type={delivery_type}, steps={len(steps)}")
    try:
        update_job_status(job_id, "running")
        for i, step_fn in enumerate(steps):
            step_fn(job_id, keyword)
            if i < len(steps) - 1:
                print(f"  (waiting {STEP_DELAY}s for rate limit...)")
                time.sleep(STEP_DELAY)
        update_job_status(job_id, "done")
        print(f"\n=== Done (job_id={job_id}) ===\n")
    except Exception as exc:
        update_job_status(job_id, "failed")
        print(f"[pipeline] ERROR job_id={job_id}: {exc}")
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


@app.get("/health")
async def health():
    return {"status": "ok"}
