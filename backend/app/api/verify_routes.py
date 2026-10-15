from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.fixer import FixError
from app.analysis.serialize import verify_dict, loop_dict
from app.verification.patch_verifier import VerifyError, verify_fix
from app.verification.fix_loop import fix_with_retries
from app.core.llm_client import LLMError, llm_for

router = APIRouter()


class VerifyFixRequest(BaseModel):
    file_path: str
    line: int
    rule_id: str
    test_code: str | None = None   # your own pytest code (e.g. passing_code from /generate-tests)
    auto_tests: bool = False       # let the model write tests for the patched function
    model: str | None = None


class FixLoopRequest(VerifyFixRequest):
    max_attempts: int = 3          # model-written fixes only; hard cap 3


def run_verify_fix(project_root: str, payload: VerifyFixRequest, progress=None) -> dict:
    llm = llm_for(payload.model)
    r = verify_fix(project_root, payload.file_path, payload.line, payload.rule_id,
                   test_code=payload.test_code, auto_tests=payload.auto_tests,
                   fix_llm=llm, test_llm=llm, progress=progress)
    return verify_dict(r, payload.file_path, project_root)


def run_fix_loop(project_root: str, payload: FixLoopRequest, progress=None) -> dict:
    llm = llm_for(payload.model)
    lr = fix_with_retries(project_root, payload.file_path, payload.line, payload.rule_id,
                          test_code=payload.test_code, auto_tests=payload.auto_tests,
                          max_attempts=payload.max_attempts, progress=progress,
                          fix_llm=llm, test_llm=llm)
    return loop_dict(lr, payload.file_path, project_root)


def _root(project_id: str) -> str:
    try:
        return str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))


@router.post("/{project_id}/verify-fix")
def verify_fix_route(project_id: str, payload: VerifyFixRequest):
    """Propose a fix, then test it: run tests on a COPY of the project before and after
    the patch. Your real project is never modified. Runs project code - trusted projects only."""
    try:
        return run_verify_fix(_root(project_id), payload)
    except (FixError, VerifyError) as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))


@router.post("/{project_id}/fix-loop")
def fix_loop_route(project_id: str, payload: FixLoopRequest):
    """Like verify-fix, but if a model-written patch fails, feed the failure back and retry
    (max 3 attempts). Every attempt is returned. Nothing is applied."""
    try:
        return run_fix_loop(_root(project_id), payload)
    except (FixError, VerifyError) as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))
