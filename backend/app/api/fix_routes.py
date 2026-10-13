from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.fixer import FixError, propose_fix
from app.core.llm_client import LLMError

router = APIRouter()


class FixRequest(BaseModel):
    file_path: str   # from a scan finding, e.g. "vulnerable_demo.py"
    line: int        # the finding's line number
    rule_id: str     # the finding's rule, e.g. "SEC007"


class FixResponse(BaseModel):
    rule_id: str
    line: int
    method: str              # "rule" = deterministic, "llm" = model-written
    verified: bool
    explanation: str
    diff: str                # unified diff — nothing is written to your project
    checks: list[str]
    warnings: list[str]
    how_to_apply: str


@router.post("/{project_id}/propose-fix", response_model=FixResponse)
def propose_fix_route(project_id: str, payload: FixRequest):
    """Propose a patch for one scanner finding. Read-only: returns a diff, changes nothing."""
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))

    try:
        p = propose_fix(project_root, payload.file_path, payload.line, payload.rule_id)
    except FixError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))

    return FixResponse(
        rule_id=p.rule_id, line=p.line, method=p.method, verified=p.verified,
        explanation=p.explanation, diff=p.diff, checks=p.checks, warnings=p.warnings,
        how_to_apply="Save the diff to fix.patch in your original repo, review it, "
                     "then run: git apply fix.patch",
    )