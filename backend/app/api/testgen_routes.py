from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.testgen.generator import (
    TestGenError,
    extract_function,
    generate_test_code,
    keep_only,
    list_test_names,
)
from app.testgen.runner import run_tests
from app.core.llm_client import LLMError

router = APIRouter()


class TestGenRequest(BaseModel):
    file_path: str            # path inside the project, e.g. "pricing_demo.py"
    function_name: str        # a top-level function in that file
    verify: bool = False      # True = actually RUN the tests (executes project code!)


class FailingTest(BaseModel):
    name: str
    reason: str


class TestGenResponse(BaseModel):
    function: str
    module: str
    verified: bool
    generated_code: str               # everything the model wrote (assembled into a file)
    passing_code: str | None = None   # only the tests that passed (verify=True)
    passing_tests: list[str] = []
    failing_tests: list[FailingTest] = []
    run_error: str | None = None
    note: str


@router.post("/{project_id}/generate-tests", response_model=TestGenResponse)
def generate_tests(project_id: str, payload: TestGenRequest):
    try:
        project_root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))

    try:
        target = extract_function(project_root, payload.file_path, payload.function_name)
        code = generate_test_code(target, project_root)
    except TestGenError as e:
        raise HTTPException(400, str(e))
    except LLMError as e:
        raise HTTPException(503, str(e))

    if not payload.verify:
        return TestGenResponse(
            function=target.name, module=target.module, verified=False,
            generated_code=code,
            note="Tests were generated but NOT run. Set verify=true to run them and "
                 "separate passing from failing (this executes code from the project — "
                 "only do it for projects you trust).",
        )

    result = run_tests(project_root, code)
    if result.run_error:
        return TestGenResponse(
            function=target.name, module=target.module, verified=False,
            generated_code=code, run_error=result.run_error,
            note="The tests could not be run, so nothing was verified.",
        )

    all_names = list_test_names(code)
    return TestGenResponse(
        function=target.name, module=target.module, verified=True,
        generated_code=code,
        passing_code=keep_only(code, result.passed) if result.passed else None,
        passing_tests=[n for n in all_names if n in result.passed],
        failing_tests=[FailingTest(name=n, reason=r) for n, r in result.failed.items()],
        note="passing_code contains only tests that passed. A failing test means either the "
             "test itself is wrong OR the function has a real bug — read the reason to decide.",
    )