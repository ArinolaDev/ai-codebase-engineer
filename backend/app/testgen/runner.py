"""
Runs a generated pytest file and reports which tests passed / failed.

SAFETY NOTE: running tests imports and executes code from the ingested
project (and code written by an LLM). Until we have the Docker sandbox
(Phase 8), only run this on projects you trust. Mitigations that ARE in
place: separate process, 30s timeout, and a minimal environment so secrets
like API keys in your shell are not passed to the code being run.
"""

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_SECONDS = 30
_PASS_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG")


@dataclass
class RunResult:
    passed: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)  # test name -> short reason
    run_error: str | None = None  # set when the tests couldn't run at all


def _minimal_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASS_ENV_KEYS if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "250"  # stop pytest truncating its summary lines (we parse them)
    return env


_RESULT_LINE = re.compile(r"::(test_\w+)(?:\[.*?\])?\s+(PASSED|FAILED|ERROR)\b")
_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR)\s+\S+::(test_\w+)(?:\[.*?\])?(?:\s+-\s+(.*))?$", re.MULTILINE)
_SECTION_HEADER = re.compile(r"^_{3,} (.+?) _{3,}$", re.MULTILINE)


def _reasons_from_failure_sections(output: str) -> dict[str, str]:
    """Fallback that doesn't depend on terminal width: the first `E   ...`
    line inside each test's section of the FAILURES block."""
    reasons: dict[str, str] = {}
    headers = list(_SECTION_HEADER.finditer(output))
    for i, h in enumerate(headers):
        m = re.search(r"(test_\w+)", h.group(1))
        if not m:
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(output)
        for line in output[h.end():end].splitlines():
            if line.startswith("E "):
                reasons.setdefault(m.group(1), line[1:].strip())
                break
    return reasons


def parse_pytest_output(output: str) -> RunResult:
    result = RunResult()
    seen_bad: set[str] = set()

    for name, status in _RESULT_LINE.findall(output):
        if status == "PASSED":
            result.passed.add(name)
        else:
            seen_bad.add(name)

    reasons = _reasons_from_failure_sections(output)
    # The one-line summary is usually nicer when it isn't truncated; prefer it.
    for name, reason in _SUMMARY_LINE.findall(output):
        if reason and reason.strip() and not reason.strip().endswith("..."):
            reasons[name] = reason.strip()

    # A parametrized test only counts as passed if EVERY case passed.
    for name in seen_bad:
        result.passed.discard(name)
        result.failed[name] = reasons.get(name) or "test failed"

    return result


def run_tests(project_root: str, test_code: str) -> RunResult:
    root = str(Path(project_root).resolve())

    with tempfile.TemporaryDirectory(prefix="aice_tests_") as tmp:
        test_file = Path(tmp) / "test_generated.py"
        test_file.write_text(test_code, encoding="utf-8")
        ini = Path(tmp) / "pytest.ini"
        ini.write_text("[pytest]\n", encoding="utf-8")  # ignore the project's own pytest config

        cmd = [
            sys.executable, "-m", "pytest", str(test_file),
            "-c", str(ini), "--rootdir", tmp,
            "-v", "-rfE", "--tb=short", "-p", "no:cacheprovider",
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=root, env=_minimal_env(),
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return RunResult(run_error=f"Tests took longer than {TIMEOUT_SECONDS}s and were stopped.")
        except OSError as e:
            return RunResult(run_error=f"Could not start pytest: {e}")

    output = proc.stdout + "\n" + proc.stderr
    result = parse_pytest_output(output)

    if not result.passed and not result.failed:
        # Nothing ran: almost always an import problem (missing package,
        # top-level code that needs something we don't have, etc.)
        tail = "\n".join(output.strip().splitlines()[-12:])
        result.run_error = (
            "The tests could not run. This usually means the function's module "
            "can't be imported here (missing package, or code that runs on import).\n" + tail
        )
    return result