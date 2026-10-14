"""
LLM-based code review (Phase 5, second layer).

The pattern scanner finds KNOWN bad patterns. This layer asks a model to spot
things rules can't: logic bugs, unhandled cases, wrong results.

A small local model hallucinates, so every claim is GROUNDED before you see it:
  - The model must copy the exact offending line out of the code.
  - We look that line up in the real source. If it isn't there, the finding is
    thrown away (the model made it up).
  - Lines the pattern scanner already flagged are dropped (no duplicates).
Surviving findings are still opinions, so they're labelled `unverified`.
"""

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.analysis.pattern_scanner import scan_python_file

MAX_FUNCTIONS = 8          # each function = one model call; keep runs short
MAX_FINDINGS_PER_FUNCTION = 5
_SEVERITIES = {"low", "medium", "high"}


class ReviewError(Exception):
    pass


@dataclass
class ReviewFinding:
    file_path: str
    function: str
    line: int
    severity: str
    issue: str
    code: str
    source: str = "llm"
    confidence: str = "unverified"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def list_functions(source: str) -> list[tuple[str, int, int]]:
    """(name, start, end) for every top-level function and class method."""
    tree = ast.parse(source)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{sub.name}", sub.lineno, sub.end_lineno or sub.lineno))
    return out


def build_prompt(name: str, numbered_source: str) -> str:
    return f"""You are a careful code reviewer. Review this Python function `{name}`.

```python
{numbered_source}
```

STEP 1 - Write one short line of notes for each check, using real values from the code:
1. Empty input (empty list / empty string / 0)
2. Division by zero
3. Index out of range (e.g. items[len(items)] is always out of range)
4. Wrong boundary comparison (> vs >=, < vs <=)
5. Result differs from what the function name says
6. None / missing value crash

STEP 2 - Write the line `FINAL JSON:` and then a JSON array of the REAL bugs your notes found.
Each item: {{"line_text": "<offending line copied EXACTLY from the code>", "issue": "<what goes wrong and for which input>", "severity": "low" | "medium" | "high"}}
If your notes found a bug, it MUST appear in the array. Use [] only if every note says "fine".
Do not report style, naming, docstrings or type hints.

Format example (for a different function):
1. Empty input: f([]) -> a[0] fails
2. Division by zero: b can be 0 -> crash
FINAL JSON:
[{{"line_text": "return a[0] / b", "issue": "Raises ZeroDivisionError when b is 0.", "severity": "high"}}]
"""


def parse_model_json(reply: str) -> list[dict]:
    """Find a JSON list of finding objects anywhere in the reply; [] if none.
    Tolerates prose around the JSON, code fences, a single object, and
    objects like {"bugs": [...]}."""
    if "FINAL JSON:" in reply:                      # ignore the model's notes
        reply = reply.rsplit("FINAL JSON:", 1)[1]
    decoder = json.JSONDecoder()
    candidates = []
    for i, ch in enumerate(reply):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(reply[i:])
            except json.JSONDecodeError:
                continue
            candidates.append(value)

    for value in candidates:
        if isinstance(value, dict):
            if "line_text" in value:
                return [value]
            for v in value.values():
                if isinstance(v, list) and any(isinstance(x, dict) and "line_text" in x for x in v):
                    return [x for x in v if isinstance(x, dict)]
        elif isinstance(value, list):
            items = [x for x in value if isinstance(x, dict)]
            if items:
                return items
    return []


def ground_findings(items: list[dict], file_path: str, function: str,
                    lines: list[str], start: int, end: int,
                    scanner_lines: set[int]) -> list[ReviewFinding]:
    """Keep only findings whose quoted line really exists inside the function."""
    index: dict[str, int] = {}
    for n in range(start, end + 1):
        key = _norm(lines[n - 1])
        if key:
            index.setdefault(key, n)

    out, seen = [], set()
    for item in items:
        quoted = _norm(str(item.get("line_text", "")))
        issue = str(item.get("issue", "")).strip()
        if not quoted or not issue:
            continue
        # the model sometimes copies the "12: " line-number prefix
        quoted = re.sub(r"^\d+\s*[:|]\s*", "", quoted)
        line = index.get(quoted)
        if line is None:
            continue                      # not in the real code => hallucinated
        if line in scanner_lines or line in seen:
            continue                      # already reported / duplicate
        seen.add(line)
        sev = str(item.get("severity", "medium")).lower()
        out.append(ReviewFinding(
            file_path=file_path, function=function, line=line,
            severity=sev if sev in _SEVERITIES else "medium",
            issue=issue[:300], code=lines[line - 1].strip()[:200],
        ))
        if len(out) >= MAX_FINDINGS_PER_FUNCTION:
            break
    return out


def review_file(project_root: str, rel_path: str, function_name: str | None = None,
                llm=None, model: str | None = None,
                raw_log: list | None = None) -> tuple[list[ReviewFinding], dict]:
    """Returns (findings, stats). stats = functions_reviewed / skipped / dropped counts."""
    root = Path(project_root).resolve()
    path = (root / rel_path).resolve()
    if root not in path.parents:
        raise ReviewError("file_path must be inside the project.")
    if not path.is_file() or path.suffix != ".py":
        raise ReviewError(f"Python file not found in project: {rel_path}")

    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        funcs = list_functions(source)
    except SyntaxError as e:
        raise ReviewError(f"Could not parse {rel_path}: {e}")
    if function_name:
        funcs = [f for f in funcs if f[0] == function_name or f[0].endswith("." + function_name)]
        if not funcs:
            raise ReviewError(f"No function named `{function_name}` in {rel_path}.")
    if not funcs:
        raise ReviewError("No functions found in that file.")

    skipped = max(0, len(funcs) - MAX_FUNCTIONS)
    funcs = funcs[:MAX_FUNCTIONS]

    if llm is None:
        from app.core.llm_client import generate
        kwargs = {"model": model} if model else {}
        llm = lambda prompt, timeout=180.0: generate(prompt, timeout=timeout, **kwargs)

    lines = source.splitlines()
    scanner_lines = {f.line for f in scan_python_file(str(path), rel_path)}

    findings: list[ReviewFinding] = []
    claimed = 0
    for name, start, end in funcs:
        numbered = "\n".join(lines[start - 1:end])   # no numbers: they confuse small models
        reply = llm(build_prompt(name, numbered), timeout=180.0)
        if raw_log is not None:
            raw_log.append({"function": name, "reply": reply[:2500]})
        items = parse_model_json(reply)
        claimed += len(items)
        findings += ground_findings(items, rel_path, name, lines, start, end, scanner_lines)

    stats = {
        "functions_reviewed": len(funcs),
        "functions_skipped": skipped,
        "model_claims": claimed,
        "kept_after_grounding": len(findings),
        "dropped": claimed - len(findings),
    }
    return findings, stats