from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.fixer import FixError, propose_fix
from app.analysis.serialize import proposal_dict, impact_for
from app.core.llm_client import LLMError, llm_for

router = APIRouter()


class FixRequest(BaseModel):
    file_path: str   # from a scan finding, e.g. "vulnerable_demo.py"
    line: int        # the finding's line number
    rule_id: str     # the finding's rule, e.g. "SEC007"
    model: str | None = None   # optional Ollama model for model-written fixes


def run_propose_fix(project_root: str, payload: FixRequest) -> dict:
    p = propose_fix(project_root, payload.file_path, payload.line, payload.rule_id,
                    llm=llm_for(payload.model))
    d = proposal_dict(p, payload.file_path)
    d["impact"] = impact_for(project_root, payload.file_path, payload.line)
    d["how_to_apply"] = ("Save the diff to fix.patch in your original repo, review it, "
                         "then run: git apply fix.patch")
    return d


@router.post("/{project_id}/propose-fix")
def propose_fix_route(project_id: str, payload: FixRequest):
    """Propose a patch for one scanner finding. Read-only: returns a diff, changes nothing."""
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    try:
        return run_propose_fix(project_root, payload)
    except FixError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))
