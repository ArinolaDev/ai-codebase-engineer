import asyncio
import json
import time
from dataclasses import asdict

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.core.jobs import store
from app.ingestion.file_walker import walk_project
from app.ingestion.ingest_service import get_project_path, IngestError
from pathlib import Path

from app.analysis import history
from app.analysis.pattern_scanner import sort_findings
from app.analysis.scan_dispatch import SCAN_LANGUAGES, scan_file
from app.api.fix_routes import FixRequest, run_propose_fix
from app.api.review_routes import ReviewRequest, run_review
from app.api.verify_routes import VerifyFixRequest, FixLoopRequest, run_verify_fix, run_fix_loop

router = APIRouter()        # mounted at /api/projects
jobs_router = APIRouter()   # mounted at /api/jobs


class ScanRequest(BaseModel):
    min_severity: str = "low"


def run_scan(project_root: str, payload: ScanRequest, progress) -> dict:
    order = {"high": 0, "medium": 1, "low": 2}
    if payload.min_severity not in order:
        raise ValueError("min_severity must be high, medium, or low")
    files = [f for f in walk_project(project_root) if f.language in SCAN_LANGUAGES]
    findings = []
    for i, f in enumerate(files, start=1):
        progress("scan", f"Scanning {f.path} ({i}/{len(files)})", n=i, of=len(files))
        findings.extend(scan_file(f.absolute_path, f.path, f.language))
    findings = [f for f in sort_findings(findings) if order[f.severity] <= order[payload.min_severity]]
    by_sev = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        by_sev[f.severity] += 1
    result = {"files_scanned": len(files), "total_findings": len(findings),
              "by_severity": by_sev, "findings": [asdict(f) for f in findings]}
    try:   # history is a bonus: a failure here must never fail the scan itself
        if payload.min_severity == "low":
            result["history"] = history.record_scan(Path(project_root).resolve().name, result)
    except Exception:
        pass
    return result


# kind -> (request model, runner(project_root, payload, progress))
KINDS = {
    "scan": (ScanRequest, run_scan),
    "review": (ReviewRequest, lambda root, p, prog: run_review(root, p, prog)),
    "propose-fix": (FixRequest, lambda root, p, prog: run_propose_fix(root, p)),
    "verify-fix": (VerifyFixRequest, lambda root, p, prog: run_verify_fix(root, p, prog)),
    "fix-loop": (FixLoopRequest, lambda root, p, prog: run_fix_loop(root, p, prog)),
}


@router.post("/{project_id}/jobs/{kind}")
def start_job(project_id: str, kind: str, payload: dict = Body(default={})):
    """Start a long operation in the background and return a job id immediately.
    kind: scan | review | propose-fix | verify-fix | fix-loop (body = the same fields as that endpoint).
    Then watch it live at GET /api/jobs/{job_id}/events, or poll GET /api/jobs/{job_id}."""
    if kind not in KINDS:
        raise HTTPException(404, f"Unknown job kind `{kind}`. Use one of: {', '.join(KINDS)}")
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    model_cls, runner = KINDS[kind]
    try:
        req = model_cls(**payload)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))

    job = store.submit(kind, lambda progress: runner(project_root, req, progress))
    return {"job_id": job.id, "status": job.status,
            "events_url": f"/api/jobs/{job.id}/events", "status_url": f"/api/jobs/{job.id}"}


@jobs_router.get("/{job_id}")
def job_status(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (the server may have restarted).")
    return job.snapshot()


@jobs_router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """Server-Sent Events: `progress` events as they happen, then one `result` (or `error`) event.
    In a browser: new EventSource('/api/jobs/<id>/events')."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (the server may have restarted).")

    async def stream():
        sent, last_beat = 0, time.time()
        while True:
            if await request.is_disconnected():
                return
            for ev in job.events_since(sent):
                yield f"event: progress\ndata: {json.dumps(ev)}\n\n"
                sent += 1
            if job.status in ("done", "error") and not job.events_since(sent):
                name = "result" if job.status == "done" else "error"
                yield f"event: {name}\ndata: {json.dumps(job.snapshot(include_events=False))}\n\n"
                return
            if time.time() - last_beat > 15:
                yield ": keepalive\n\n"
                last_beat = time.time()
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
