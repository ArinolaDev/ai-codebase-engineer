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
    use_context: bool = False          # True = tell the model who calls this function and what it calls


def run_review(project_root: str, payload: ReviewRequest, progress=None) -> dict:
    raw: list = []
    findings, stats = review_file(project_root, payload.file_path, payload.function_name,
                                  model=payload.model, raw_log=raw, progress=progress,
                                  use_context=payload.use_context)
    return {
        "findings": [vars(f) for f in findings],
        "stats": stats,
        "raw_replies": raw if payload.debug else None,
        "note": "These are model opinions: every quoted line was verified to exist, but the "
                "claim itself is unverified. Treat them as leads to check, not facts.",
    }


@router.post("/{project_id}/llm-review")
def llm_review(project_id: str, payload: ReviewRequest):
    """Model-based review for logic bugs the pattern scanner can't see.
    Every finding is checked against the real source; hallucinated ones are dropped."""
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    try:
        return run_review(project_root, payload)
    except ReviewError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))
