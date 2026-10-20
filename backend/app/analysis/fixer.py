"""
Fix proposals for scanner findings (Phase 7).

Design rules (learned from Phase 6: a small local model can't be trusted):
  1. Never ask the model for a diff. We compute the diff ourselves with difflib.
  2. Easy, mechanical rules get a deterministic fix (no LLM, always correct).
  3. Everything else: the LLM rewrites ONLY the enclosing function/statement,
     and we then CHECK the result before showing it:
        - it parses,
        - a function keeps its name and parameters,
        - re-running the scanner shows the target finding is gone,
        - re-running the scanner shows no NEW finding appeared.
  4. We never write to the project. We return a unified diff; a human applies it.

A proposal that fails a check is still returned (so you can read it) but with
verified=False and the failed checks listed.
"""

import ast
import difflib
import re
import tempfile
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.pattern_scanner import Finding, scan_python_file


class FixError(Exception):
    pass


@dataclass
class FixProposal:
    rule_id: str
    line: int
    method: str                    # "rule" (deterministic) or "llm"
    diff: str                      # unified diff, applicable with `git apply`
    explanation: str
    verified: bool
    checks: list[str] = field(default_factory=list)   # what was checked / what failed
    warnings: list[str] = field(default_factory=list)
    new_source: str = ""           # full patched file (used by the verifier; not shown to users)


# --- locating the finding ---------------------------------------------------

def _safe_path(project_root: str, rel_path: str) -> Path:
    root = Path(project_root).resolve()
    p = (root / rel_path).resolve()
    if root not in p.parents:
        raise FixError("file_path must be inside the project.")
    if not p.is_file() or p.suffix != ".py":
        raise FixError(f"Python file not found in project: {rel_path}")
    return p


def find_finding(path: Path, rel_path: str, line: int, rule_id: str) -> Finding:
    for f in scan_python_file(str(path), rel_path):
        if f.line == line and f.rule_id == rule_id:
            return f
    raise FixError(
        f"No {rule_id} finding at line {line} of {rel_path}. "
        "Run the scan again — the file may have changed."
    )


# --- deterministic fixes ----------------------------------------------------
# Each returns (new_line, explanation, warnings) or None if the line doesn't match.

def _fix_bare_except(line: str):
    m = re.match(r"^(\s*)except\s*:(.*)$", line)
    if not m:
        return None
    return (
        f"{m.group(1)}except Exception:{m.group(2)}",
        "`except Exception:` still handles ordinary errors but no longer swallows "
        "KeyboardInterrupt and SystemExit. Narrow it further to the exact exceptions you expect.",
        [],
    )


def _fix_weak_hash(line: str):
    new = re.sub(r"\bhashlib\.(md5|sha1)\(", "hashlib.sha256(", line)
    if new == line:
        return None
    return (
        new,
        "SHA-256 replaces the broken MD5/SHA-1 for integrity-style hashing.",
        ["This changes the hash output. Anything that stored or compares old hashes "
         "(database rows, signatures, cache keys) will no longer match.",
         "If this hashes PASSWORDS, SHA-256 is still the wrong tool: use bcrypt or argon2."],
    )


def _fix_verify_false(line: str):
    new = re.sub(r"\bverify\s*=\s*False\b", "verify=True", line)
    if new == line:
        return None
    return (
        new,
        "TLS certificate verification is turned back on.",
        ["If this endpoint uses a self-signed or private-CA certificate, requests will now "
         "fail; point `verify=` at the CA bundle path instead."],
    )


def _fix_debug_true(line: str):
    new = re.sub(r"\bdebug\s*=\s*True\b", "debug=False", line)
    if new == line:
        return None
    return (
        new,
        "Debug mode is switched off so the interactive debugger isn't exposed.",
        ["If you rely on debug mode locally, read it from an environment variable instead."],
    )


_RULE_FIXERS = {
    "BUG001": _fix_bare_except,
    "SEC007": _fix_weak_hash,
    "SEC008": _fix_verify_false,
    "SEC009": _fix_debug_true,
}


# --- scope for LLM fixes ----------------------------------------------------

def find_scope(source: str, line: int) -> tuple[int, int, bool]:
    """(start, end, is_function): the innermost function containing `line`,
    else the top-level statement containing it. 1-indexed, inclusive."""
    tree = ast.parse(source)
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            end = node.end_lineno or node.lineno
            if start <= line <= end and (best is None or end - start < best[1] - best[0]):
                best = (start, end)
    if best:
        return best[0], best[1], True
    for node in tree.body:
        end = node.end_lineno or node.lineno
        if node.lineno <= line <= end:
            return node.lineno, end, False
    raise FixError("Couldn't find the code around that line.")


def build_prompt(finding: Finding, snippet: str, feedback: str | None = None) -> str:
    retry = (f"\nYour PREVIOUS attempt was rejected. Reason:\n{feedback}\nFix that problem this time.\n" if feedback else "")
    return f"""You are fixing ONE specific problem in Python code.

Problem ({finding.rule_id}): {finding.title}
Why it is a problem: {finding.explanation}
Direction for the fix: {finding.suggestion}
The offending line is: {finding.code}
{retry}
Code:
```python
{snippet}
```

Rules:
- Output ONLY the full corrected code in one ```python block. No explanation.
- Change as little as possible. Keep the function name, parameters and everything unrelated exactly the same.
- Do NOT write import statements. If you use `os`, `ast`, `subprocess`, `shlex`, `secrets` or `json`, assume they are already imported.
- Do NOT remove the functionality; fix the problem while keeping the code useful.
"""


def _extract_code(reply: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", reply, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    text = re.sub(r"^```(?:python|py)?\s*\n", "", reply.strip())
    return text.strip("\n")


# modules we'll auto-import if the fixed code uses them and the file doesn't
_AUTO_IMPORT = {"ast", "os", "subprocess", "shlex", "secrets", "json", "hashlib"}


def _add_missing_imports(source: str) -> tuple[str, list[str]]:
    tree = ast.parse(source)
    imported = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported |= {a.asname or a.name for a in n.names}
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    missing = sorted((used & _AUTO_IMPORT) - imported)
    if not missing:
        return source, []

    insert_after = 0
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            insert_after = n.end_lineno or n.lineno
        elif (insert_after == 0 and isinstance(n, ast.Expr)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
            insert_after = n.end_lineno or n.lineno   # module docstring
    lines = source.splitlines()
    new_lines = lines[:insert_after] + [f"import {m}" for m in missing] + lines[insert_after:]
    return "\n".join(new_lines) + ("\n" if source.endswith("\n") else ""), missing


# --- verification -----------------------------------------------------------

def _finding_counts(source: str) -> Counter:
    with tempfile.TemporaryDirectory(prefix="aice_fix_") as tmp:
        p = Path(tmp) / "check.py"
        p.write_text(source, encoding="utf-8")
        return Counter(f.rule_id for f in scan_python_file(str(p), "check.py"))


def verify_fix(old_source: str, new_source: str, finding: Finding) -> list[str]:
    """Return a list of FAILED checks (empty = all good)."""
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        return [f"The patched file is not valid Python (line {e.lineno}: {e.msg})."]

    failures = []
    before, after = _finding_counts(old_source), _finding_counts(new_source)
    # Counted per rule (not per line) so harmless line edits don't confuse the check.
    if after[finding.rule_id] >= before[finding.rule_id]:
        failures.append(f"The {finding.rule_id} problem is still detected after the change.")
    new_problems = [r for r in after if r != finding.rule_id and after[r] > before.get(r, 0)]
    if new_problems:
        failures.append("The change introduced new findings: " + ", ".join(sorted(new_problems)))
    return failures


def _tidy_fstrings(old: str, new: str) -> tuple[str, int]:
    """Drop the `f` from f-strings that have no {placeholders}, but only on lines the model wrote
    or changed (code it didn't touch is never edited). Small models often turn
    f"... {x}" into "... ?" and forget the `f`: harmless to run, but a linter error."""
    try:
        tree = ast.parse(new)
    except SyntaxError:
        return new, 0
    old_lines, new_lines = old.splitlines(), new.splitlines()
    changed: set[int] = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag in ("replace", "insert"):
            changed.update(range(j1 + 1, j2 + 1))

    edits: list[tuple[int, int]] = []          # (line index, byte column of the `f`)
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or node.lineno != node.end_lineno or node.lineno not in changed:
            continue
        if any(isinstance(v, ast.FormattedValue) for v in node.values):
            continue
        raw = new_lines[node.lineno - 1].encode("utf-8")
        seg = raw[node.col_offset:node.end_col_offset]
        m = re.match(rb"([A-Za-z]{1,2})['\"]", seg)
        if not m or b"f" not in m.group(1).lower() or b"{" in seg or b"}" in seg:
            continue                            # braces here are {{escapes}}: leave them alone
        edits.append((node.lineno - 1, node.col_offset + m.group(1).lower().index(b"f")))

    for idx, pos in sorted(set(edits), key=lambda e: (e[0], -e[1])):
        raw = new_lines[idx].encode("utf-8")
        new_lines[idx] = (raw[:pos] + raw[pos + 1:]).decode("utf-8")
    out = "\n".join(new_lines) + ("\n" if new.endswith("\n") else "")
    return out, len(set(edits))


def _signature(source: str):
    """(name, [param names]) of the single top-level def in `source`, or None."""
    try:
        body = ast.parse(textwrap.dedent(source)).body
    except SyntaxError:
        return None
    defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(body) != 1 or len(defs) != 1:
        return None
    a = defs[0].args
    params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    params += [x.arg for x in (a.vararg, a.kwarg) if x]
    return defs[0].name, params


def make_diff(rel_path: str, old: str, new: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", lineterm="",
        )
    ) + "\n"


# --- public entry point -----------------------------------------------------

def propose_fix(project_root: str, rel_path: str, line: int, rule_id: str, llm=None,
                feedback: str | None = None) -> FixProposal:
    path = _safe_path(project_root, rel_path)
    finding = find_finding(path, rel_path, line, rule_id)
    old_source = path.read_text(encoding="utf-8", errors="replace")
    old_lines = old_source.splitlines()
    trailing_nl = old_source.endswith("\n")

    # 1) deterministic fix
    fixer = _RULE_FIXERS.get(rule_id)
    if fixer:
        result = fixer(old_lines[line - 1])
        if result:
            new_line, why, warnings = result
            new_lines = old_lines[:line - 1] + [new_line] + old_lines[line:]
            new_source = "\n".join(new_lines) + ("\n" if trailing_nl else "")
            failures = verify_fix(old_source, new_source, finding)
            return FixProposal(
                rule_id=rule_id, line=line, method="rule",
                diff=make_diff(rel_path, old_source, new_source),
                explanation=why, verified=not failures,
                checks=failures or ["Patched file parses.", "Original finding is gone.",
                                    "No new findings introduced."],
                warnings=warnings, new_source=new_source,
            )
        # line shape not recognised (e.g. multi-line call): fall through to the LLM

    # 2) LLM fix of the enclosing scope only
    if llm is None:
        from app.core.llm_client import generate as llm  # lazy import keeps module testable alone

    start, end, is_function = find_scope(old_source, line)
    snippet = "\n".join(old_lines[start - 1:end])
    indent = re.match(r"\s*", old_lines[start - 1]).group(0)

    reply = llm(build_prompt(finding, textwrap.dedent(snippet), feedback), timeout=180.0)
    new_snippet = _extract_code(reply)
    if not new_snippet.strip():
        raise FixError("The model returned an empty reply. Try again.")
    new_snippet, tidied = _tidy_fstrings(textwrap.dedent(snippet), textwrap.dedent(new_snippet))
    new_snippet = textwrap.indent(new_snippet, indent)

    checks_failed = []
    if is_function:
        old_sig, new_sig = _signature(snippet), _signature(new_snippet)
        if new_sig is None:
            checks_failed.append("The model's reply isn't a single function; rejected.")
        elif old_sig != new_sig:
            checks_failed.append(
                f"The model changed the function signature {old_sig} -> {new_sig}."
            )

    new_lines = old_lines[:start - 1] + new_snippet.splitlines() + old_lines[end:]
    new_source = "\n".join(new_lines) + ("\n" if trailing_nl else "")
    warnings = []
    if tidied:
        warnings.append(f"Removed a leftover `f` prefix from {tidied} string(s) that no longer have placeholders.")
    try:
        new_source, added = _add_missing_imports(new_source)
        if added:
            warnings.append("Added missing import(s): " + ", ".join(added) + ".")
    except SyntaxError:
        pass  # verify_fix below reports the syntax error

    checks_failed += verify_fix(old_source, new_source, finding)
    warnings.append(
        "Generated by a small local model. Read the diff before applying: the scanner "
        "check proves the pattern is gone, not that the new code behaves correctly."
    )
    return FixProposal(
        rule_id=rule_id, line=line, method="llm",
        diff=make_diff(rel_path, old_source, new_source),
        explanation=f"{finding.title}: {finding.suggestion}",
        verified=not checks_failed,
        checks=checks_failed or ["Patched file parses.", "Original finding is gone.",
                                 "No new findings introduced.",
                                 "Function name and parameters unchanged."],
        warnings=warnings, new_source=new_source,
    )
