"""
Code Review Automation — FastAPI Server
Run with: python server.py
Then open: http://localhost:7000
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from config import settings
from ingestion import ingest_folder, ingest_zip_base64
from models import ReviewReport, ReviewRequest, ReviewStatusResponse
from report_builder import build_html_report
from reviewer import run_review

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    filename=Path(__file__).parent / "code_reviewer.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────
app = FastAPI(
    title="Enterprise Code Review Automation",
    description="Automated code review using local static analysis",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{settings.PORT}", f"http://127.0.0.1:{settings.PORT}"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── In-memory job store ───────────────────────────────────────
# { job_id: ReviewStatusResponse }
_jobs: dict[str, ReviewStatusResponse] = {}
_executor = ThreadPoolExecutor(max_workers=3)


# ── Background review worker ──────────────────────────────────

def _run_review_job(job_id: str, source_type: str, folder_path: Optional[str],
                    zip_base64: Optional[str], repo_name: Optional[str]) -> None:
    job = _jobs[job_id]
    job.status = "running"

    def _progress(msg: str) -> None:
        job.progress = msg
        logger.info("[%s] %s", job_id, msg)

    try:
        _progress("Ingesting source files…")
        if source_type == "folder":
            name, files = ingest_folder(folder_path, repo_name or "")
        else:
            name = repo_name or "uploaded_repo"
            _, files = ingest_zip_base64(zip_base64, name)

        if not files:
            raise ValueError("No reviewable source files found in the repository.")

        # Tell the user which review engine will be used
        from config import settings as _s
        if _s.GROQ_API_KEY:
            _progress("Using Groq AI (cloud) for review…")
        else:
            _progress("No AI model configured — using built-in static analysis…")

        report = run_review(
            files=files,
            repo_name=name,
            repo_source=source_type,
            progress_cb=_progress,
        )
        job.report = report
        job.status = "done"
        job.progress = "Review complete!"
        logger.info("[%s] Review done — %d findings", job_id, len(report.findings))

    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        logger.exception("[%s] Review failed: %s", job_id, exc)


# ── API routes ────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/review", response_model=ReviewStatusResponse)
def start_review(req: ReviewRequest):
    """Start an async review job. Returns a job_id to poll."""
    if req.source_type == "folder" and not req.folder_path:
        raise HTTPException(400, detail="folder_path is required for source_type=folder")
    if req.source_type == "zip" and not req.zip_base64:
        raise HTTPException(400, detail="zip_base64 is required for source_type=zip")

    job_id = str(uuid.uuid4())
    status = ReviewStatusResponse(job_id=job_id, status="queued", progress="Queued…")
    _jobs[job_id] = status

    _executor.submit(
        _run_review_job,
        job_id,
        req.source_type,
        req.folder_path,
        req.zip_base64,
        req.repo_name,
    )
    logger.info("Review job queued: id=%s source=%s", job_id, req.source_type)
    return status


@app.post("/api/review/upload", response_model=ReviewStatusResponse)
async def start_review_upload(
    file: UploadFile = File(...),
    repo_name: str = Form("uploaded_repo"),
):
    """Accept a .zip file upload directly (multipart/form-data)."""
    import base64
    data = await file.read()
    b64 = base64.b64encode(data).decode()

    job_id = str(uuid.uuid4())
    status = ReviewStatusResponse(job_id=job_id, status="queued", progress="Queued…")
    _jobs[job_id] = status

    _executor.submit(
        _run_review_job,
        job_id,
        "zip_base64",
        None,
        b64,
        repo_name,
    )
    logger.info("Review upload job queued: id=%s name=%s", job_id, repo_name)
    return status


@app.get("/api/review/{job_id}", response_model=ReviewStatusResponse)
def get_status(job_id: str):
    """Poll the status of a review job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job


@app.get("/api/review/{job_id}/view", response_class=HTMLResponse)
def view_report(job_id: str):
    """Serve the finished report inline in the browser."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.status != "done" or not job.report:
        raise HTTPException(400, detail="Report not ready yet")
    return build_html_report(job.report)


@app.get("/api/review/{job_id}/html", response_class=Response)
def download_report_html(job_id: str):
    """Download the finished report as a self-contained HTML file."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.status != "done" or not job.report:
        raise HTTPException(400, detail="Report not ready yet")
    html = build_html_report(job.report)
    filename = f"review_{job.report.repo_name}_{job_id[:8]}.html"
    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/review/{job_id}/json")
def download_report_json(job_id: str):
    """Download the finished report as JSON."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    if job.status != "done" or not job.report:
        raise HTTPException(400, detail="Report not ready yet")
    return job.report


# ── Frontend ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    path = Path(__file__).parent / "index.html"
    return path.read_text(encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"\n  Code Review Automation running at http://{settings.HOST}:{settings.PORT}\n")
    uvicorn.run("server:app", host=settings.HOST, port=settings.PORT, reload=False)
