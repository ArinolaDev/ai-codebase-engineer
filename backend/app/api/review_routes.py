from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.llm_review import ReviewError, review_file
from app.core.llm_client import LLMError

router = APIRouter()


class ReviewRequest(BaseModel):
    file_path: str
    function_name: str | None = None   # omit = review every function (max 8)
    model: str | None = None           # e.g. "qwen2.5-coder:3b"; default = llama3.2:1b
    debug: bool = False                # True = include the model's raw replies


class ReviewItem(BaseModel):
    file_path: str
    function: str
    line: int
    severity: str
    issue: str
    code: str
    source: str
    confidence: str


class ReviewResponse(BaseModel):
    findings: list[ReviewItem]
    stats: dict
    note: str
    raw_replies: list[dict] | None = None   # only when debug=true


@router.post("/{project_id}/llm-review", response_model=ReviewResponse)
def llm_review(project_id: str, payload: ReviewRequest):
    """Model-based review for logic bugs the pattern scanner can't see.
    Every finding is checked against the real source; hallucinated ones are dropped."""
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    try:
        raw: list = []
        findings, stats = review_file(project_root, payload.file_path,
                                      payload.function_name, model=payload.model,
                                      raw_log=raw)
    except ReviewError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))

    return ReviewResponse(
        findings=[ReviewItem(**vars(f)) for f in findings],
        stats=stats,
        raw_replies=raw if payload.debug else None,
        note="These are model opinions: every quoted line was verified to exist, but the "
             "claim itself is unverified. Treat them as leads to check, not facts.",
    )