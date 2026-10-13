"""
Test generation for a single Python function.

Flow:
  1. Pull the function's source out of the project (stdlib `ast`, exact for Python).
  2. Ask the LLM for pytest tests.
  3. Clean the model's output into a runnable test file.
  4. (optional) run the tests and split them into passing / failing — see runner.py

A small local model will sometimes write tests with wrong expectations. That
is why verification exists: tests that pass are kept, tests that fail are
reported separately. A failing test means EITHER the test is wrong OR the
function has a real bug — we can't tell which automatically, so we say so.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path


class TestGenError(Exception):
    pass


@dataclass
class TargetFunction:
    name: str
    module: str          # importable dotted module path, e.g. "pkg.pricing"
    source: str          # full source of the function (incl. decorators)
    start_line: int
    end_line: int


def _module_name(rel_path: str) -> str:
    p = Path(rel_path)
    if p.suffix != ".py":
        raise TestGenError("Only .py files are supported right now.")
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    module = ".".join(parts)
    if not module or not all(part.isidentifier() for part in parts):
        raise TestGenError(
            f"`{rel_path}` can't be imported as a Python module "
            "(folder or file names must be valid identifiers, no hyphens or spaces)."
        )
    return module


def extract_function(project_root: str, rel_path: str, function_name: str) -> TargetFunction:
    root = Path(project_root).resolve()
    file_path = (root / rel_path).resolve()
    # Stay inside the project: reject ../ tricks and absolute paths
    if root not in file_path.parents:
        raise TestGenError("file_path must be inside the project.")
    if not file_path.is_file():
        raise TestGenError(f"File not found in project: {rel_path}")

    source = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise TestGenError(f"Could not parse {rel_path}: {e}")

    lines = source.splitlines()
    for node in tree.body:  # top-level only
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            end = node.end_lineno or node.lineno
            return TargetFunction(
                name=function_name,
                module=_module_name(rel_path),
                source="\n".join(lines[start - 1:end]),
                start_line=start,
                end_line=end,
            )

    available = [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    hint = f" Top-level functions in that file: {', '.join(available)}." if available else ""
    raise TestGenError(
        f"No top-level function named `{function_name}` in {rel_path}. "
        f"(Methods inside classes aren't supported yet.){hint}"
    )


def build_prompt(target: TargetFunction) -> str:
    return f"""You are writing pytest unit tests.

Function under test (from module `{target.module}`):
```python
{target.source}
```

Write 4 to 6 pytest test functions covering:
- normal inputs
- edge cases
- invalid inputs (use pytest.raises ONLY if the code above clearly raises)
- boundary values

Rules:
- Output ONLY one ```python code block. No explanation before or after.
- Do NOT import `{target.name}`; it is already imported for you.
- Every test is a top-level function named test_something.
- Only assert behavior you can verify from the code above. Do not guess.
- Write expected values as literal numbers or strings that you worked out by hand
  (e.g. `assert f(200, 10) == 180.0`). NEVER compute the expected value with the
  same formula the function uses; such a test can never catch a wrong formula.
"""


def extract_code_block(llm_output: str) -> str:
    """Pull the python code out of the model's reply (fenced or not)."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", llm_output, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    # Unclosed fence (model got cut off) or no fence at all
    text = re.sub(r"^```(?:python|py)?\s*\n", "", llm_output.strip())
    return text.strip("\n")


def assemble_test_file(target: TargetFunction, project_root: str, model_code: str) -> str:
    # We provide the import ourselves, so drop any the model wrote for the same module.
    drop = re.compile(rf"^\s*from\s+{re.escape(target.module)}\s+import\s+.*$")
    cleaned = "\n".join(l for l in model_code.splitlines() if not drop.match(l))

    header = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(project_root).resolve())!r})\n"
        "import pytest\n"
        f"from {target.module} import {target.name}\n\n\n"
    )
    return header + cleaned.strip("\n") + "\n"


def list_test_names(code: str) -> list[str]:
    tree = ast.parse(code)  # raises SyntaxError if the model produced invalid code
    return [
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
    ]


def keep_only(code: str, keep_tests: set[str]) -> str:
    """Rebuild the file with every non-test statement (imports, fixtures,
    helpers) plus only the test functions named in `keep_tests`."""
    tree = ast.parse(code)
    lines = code.splitlines()
    chunks = []
    for node in tree.body:
        is_test = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        if is_test and node.name not in keep_tests:
            continue
        start = node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        chunks.append("\n".join(lines[start - 1:node.end_lineno]))
    return "\n\n".join(chunks) + "\n"


def generate_test_code(target: TargetFunction, project_root: str, llm=None) -> str:
    """Ask the LLM and return an assembled, syntactically valid test file."""
    if llm is None:
        from app.core.llm_client import generate as llm  # lazy: keeps this module testable alone

    reply = llm(build_prompt(target), timeout=180.0)
    model_code = extract_code_block(reply)
    if not model_code.strip():
        raise TestGenError("The model returned an empty reply. Try again.")

    code = assemble_test_file(target, project_root, model_code)
    try:
        names = list_test_names(code)
    except SyntaxError as e:
        raise TestGenError(
            f"The model wrote invalid Python (line {e.lineno}: {e.msg}). "
            "Small models do this sometimes — try again."
        )
    if not names:
        raise TestGenError("The model's reply contained no test_ functions. Try again.")
    return code