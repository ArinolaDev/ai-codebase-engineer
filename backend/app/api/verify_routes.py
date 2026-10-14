from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.fixer import FixError
from app.verification.patch_verifier import VerifyError, verify_fix
from app.core.llm_client import LLMError

router = APIRouter()


class VerifyFixRequest(BaseModel):
    file_path: str
    line: int
    rule_id: str
    test_code: str | None = None   # your own pytest code (e.g. passing_code from /generate-tests)
    auto_tests: bool = False       # let the model write tests for the patched function


class VerifyFixResponse(BaseModel):
    verdict: str                   # safe_by_tests | unsafe | untested | rejected
    summary: str
    diff: str
    fix_method: str
    fix_checks: list[str]
    fix_warnings: list[str]
    tests_before_passed: list[str]
    tests_after_passed: list[str]
    regressions: dict[str, str]
    notes: list[str]
    test_code_used: str | None = None
    safety_note: str


@router.post("/{project_id}/verify-fix", response_model=VerifyFixResponse)
def verify_fix_route(project_id: str, payload: VerifyFixRequest):
    """Propose a fix, then test it: run tests on a COPY of the project before and after
    the patch. Your real project is never modified. Runs project code - trusted projects only."""
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    try:
        r = verify_fix(project_root, payload.file_path, payload.line, payload.rule_id,
                       test_code=payload.test_code, auto_tests=payload.auto_tests)
    except (FixError, VerifyError) as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))

    p = r.proposal
    return VerifyFixResponse(
        verdict=r.verdict, summary=r.summary, diff=p.diff, fix_method=p.method,
        fix_checks=p.checks, fix_warnings=p.warnings,
        tests_before_passed=r.tests_before_passed, tests_after_passed=r.tests_after_passed,
        regressions=r.regressions, notes=r.notes, test_code_used=r.test_code_used,
        safety_note="Tests ran in a temporary copy, not a sandbox. Only use on projects you trust.",
    )