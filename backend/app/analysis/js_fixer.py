"""
Fix proposals for JavaScript / TypeScript findings. Same rules as the Python fixer:
  1. We compute the diff ourselves; the model never writes a diff.
  2. Mechanical rules get a deterministic fix (tree-sitter edits at exact byte positions).
  3. Everything else: the model rewrites ONLY the enclosing function (or top-level statement),
     then we CHECK it: it parses, the function keeps its name and parameters, the target finding
     is gone, and no new kind of finding appeared.
  4. Nothing is written to the project: we return a diff for a human to apply.
"""

import re
import tempfile
import textwrap
from collections import Counter
from pathlib import Path

from app.analysis.fixer import FixError, FixProposal, make_diff
from app.analysis.js_scanner import scan_js_file
from app.analysis.pattern_scanner import Finding, _KEY_PATTERNS, _SECRET_NAME
from app.parsing.js_parser import get_js_parser, parse_js_file

JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_FUNCTION_NODES = {"function_declaration", "generator_function_declaration", "method_definition",
                   "arrow_function", "function_expression", "function", "generator_function"}


def is_js_path(rel_path: str) -> bool:
    return rel_path.lower().endswith(JS_SUFFIXES)


def _safe_path(project_root: str, rel_path: str) -> Path:
    root = Path(project_root).resolve()
    p = (root / rel_path).resolve()
    if root not in p.parents:
        raise FixError("file_path must be inside the project.")
    if not p.is_file() or p.suffix.lower() not in JS_SUFFIXES:
        raise FixError(f"JavaScript/TypeScript file not found in project: {rel_path}")
    return p


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _apply_edits(src: bytes, edits: list[tuple[int, int, str]]) -> str:
    """edits are (start_byte, end_byte, replacement); applied from the end so offsets stay valid."""
    for start, end, repl in sorted(edits, key=lambda e: -e[0]):
        src = src[:start] + repl.encode("utf-8") + src[end:]
    return src.decode("utf-8")


# --- deterministic fixes ----------------------------------------------------
# Each returns (new_source, explanation, warnings) or None when the code isn't the expected shape.

def _env_name(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper() or "SECRET"


def _fix_secret(source: str, path: str, finding: Finding):
    src = source.encode("utf-8")
    root = get_js_parser(path).parse(src).root_node
    edits = []
    for n in _walk(root):
        if n.type != "string" or n.start_point[0] + 1 != finding.line:
            continue
        parent = n.parent
        name = None
        if parent is not None and parent.type == "variable_declarator":
            nn = parent.child_by_field_name("name")
            name = _txt(nn, src) if nn is not None else None
        elif parent is not None and parent.type in ("pair", "public_field_definition", "field_definition"):
            k = parent.child_by_field_name("key") or parent.child_by_field_name("name") or parent.child_by_field_name("property")
            name = _txt(k, src).strip("'\"`") if k is not None else None
        elif parent is not None and parent.type == "assignment_expression":
            l = parent.child_by_field_name("left")
            if l is not None:
                prop = l.child_by_field_name("property") if l.type == "member_expression" else l
                name = _txt(prop, src) if prop is not None else None
        value = _txt(n, src)
        is_known_key = any(p.search(value) for _, p in _KEY_PATTERNS)
        if is_known_key or (name and _SECRET_NAME.search(name) and len(value) > 7):
            edits.append((n.start_byte, n.end_byte, f"process.env.{_env_name(name or 'SECRET_KEY')}"))
    if not edits:
        return None
    return (_apply_edits(src, edits),
            "The secret is read from an environment variable instead of being written in the code.",
            ["The value you had here is still in your git history. Rotate it (make a new one) and "
             "treat the old one as leaked.",
             "Set the environment variable where the app runs. `process.env` exists in Node; "
             "browser code needs your bundler's own way to inject settings."])


def _fix_loose_equality(source: str, path: str, finding: Finding):
    src = source.encode("utf-8")
    root = get_js_parser(path).parse(src).root_node
    edits = []
    for n in _walk(root):
        if n.type == "binary_expression" and n.start_point[0] + 1 == finding.line:
            op = n.child_by_field_name("operator")
            if op is not None and _txt(op, src) in ("==", "!="):
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                if any(x is not None and (x.type == "null" or _txt(x, src) == "undefined") for x in (l, r)):
                    continue   # `x == null` is the idiom for "null or undefined": leave it as it is
                edits.append((op.start_byte, op.end_byte, "===" if _txt(op, src) == "==" else "!=="))
    if not edits:
        return None
    return (_apply_edits(src, edits),
            "`===` compares without converting types, so `0 === ''` is false as you would expect.",
            ["Strict equality can change results where the old code relied on conversion, "
             "for example comparing the string \"5\" with the number 5."])


def _fix_debugger(source: str, path: str, finding: Finding):
    lines = source.splitlines()
    i = finding.line - 1
    if not (0 <= i < len(lines)) or "debugger" not in lines[i]:
        return None
    if lines[i].strip() in ("debugger;", "debugger"):
        del lines[i]
    else:
        lines[i] = re.sub(r"\bdebugger\s*;?\s*", "", lines[i])
    return ("\n".join(lines) + ("\n" if source.endswith("\n") else ""),
            "The leftover `debugger` statement is removed.", [])


def _fix_empty_catch(source: str, path: str, finding: Finding):
    src = source.encode("utf-8")
    root = get_js_parser(path).parse(src).root_node
    for n in _walk(root):
        if n.type == "catch_clause" and n.start_point[0] + 1 == finding.line:
            body = n.child_by_field_name("body")
            if body is None or body.named_child_count:
                continue
            param = n.child_by_field_name("parameter")
            name = _txt(param, src) if param is not None and param.type == "identifier" else "error"
            start = n.start_byte
            new_clause = f"catch ({name}) {{ console.error({name}); }}"
            return (_apply_edits(src, [(start, n.end_byte, new_clause)]),
                    "The error is now logged instead of silently swallowed.",
                    ["Logging is the minimum. Decide whether this should be handled, retried or rethrown."])
    return None


def _regex_line_fix(pattern: str, repl, why: str, warnings: list[str]):
    rx = re.compile(pattern)

    def fix(source: str, path: str, finding: Finding):
        lines = source.splitlines()
        i = finding.line - 1
        if not (0 <= i < len(lines)):
            return None
        new = rx.sub(repl, lines[i])
        if new == lines[i]:
            return None
        lines[i] = new
        return "\n".join(lines) + ("\n" if source.endswith("\n") else ""), why, warnings
    return fix


_fix_weak_hash = _regex_line_fix(
    r"""createHash\(\s*(['"])(?:md5|sha1)\1\s*\)""", lambda m: f"createHash({m.group(1)}sha256{m.group(1)})",
    "SHA-256 replaces the broken MD5/SHA-1 for integrity-style hashing.",
    ["This changes the hash output. Anything that stored or compares old hashes "
     "(database rows, signatures, cache keys) will no longer match.",
     "If this hashes PASSWORDS, SHA-256 is still the wrong tool: use bcrypt, scrypt or argon2."])

_fix_tls_option = _regex_line_fix(
    r"""\brejectUnauthorized(['"]?)\s*:\s*false\b""", lambda m: f"rejectUnauthorized{m.group(1)}: true",
    "TLS certificate verification is turned back on.",
    ["If this endpoint uses a self-signed or private-CA certificate, connections will now fail; "
     "give the client that CA certificate instead."])

_fix_tls_env = _regex_line_fix(
    r"""(NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*)(['"])0\2""", lambda m: f"{m.group(1)}{m.group(2)}1{m.group(2)}",
    "TLS certificate verification is turned back on.",
    ["If this endpoint uses a self-signed or private-CA certificate, connections will now fail."])

_fix_timer_string = _regex_line_fix(
    r"""\b(setTimeout|setInterval)\(\s*(['"])([^'"`\\$]*)\2\s*,""", lambda m: f"{m.group(1)}(() => {{ {m.group(3).rstrip(';')}; }},",
    "The string is replaced by a real function, so nothing is evaluated as text.",
    ["The code now runs in this scope, not the global one. If it referred to a global by name, check it still resolves."])


def _fix_tls(source, path, finding):
    return _fix_tls_option(source, path, finding) or _fix_tls_env(source, path, finding)


def _fix_secret_or_key(source, path, finding):
    return _fix_secret(source, path, finding)


_RULE_FIXERS = {
    "SEC001": _fix_secret_or_key,
    "SEC002": _fix_secret,
    "SEC004": _fix_timer_string,     # only the setTimeout("string") shape; eval/new Function go to the model
    "SEC007": _fix_weak_hash,
    "SEC008": _fix_tls,
    "BUG003": _fix_loose_equality,
    "BUG004": _fix_empty_catch,
    "BUG005": _fix_debugger,
}


# --- scope for model fixes --------------------------------------------------

def find_js_scope(source: str, path: str, line: int) -> tuple[int, int, bool, bool]:
    """(start_line, end_line, is_function, is_method): the innermost function containing `line`
    (widened to its `const f = ...;` / `export` statement), else the top-level statement."""
    src = source.encode("utf-8")
    root = get_js_parser(path).parse(src).root_node
    best = None
    for n in _walk(root):
        if n.type in _FUNCTION_NODES and n.start_point[0] + 1 <= line <= n.end_point[0] + 1:
            if best is None or (n.end_byte - n.start_byte) < (best.end_byte - best.start_byte):
                best = n
    if best is not None:
        is_method = best.type == "method_definition"
        anchor = best
        # widen to the statement that declares it, so `const f = () => {...};` stays whole
        while anchor.parent is not None and anchor.parent.type in (
                "variable_declarator", "lexical_declaration", "variable_declaration",
                "assignment_expression", "expression_statement", "export_statement", "parenthesized_expression"):
            anchor = anchor.parent
        if anchor.parent is not None and anchor.parent.type not in ("program", "statement_block", "class_body"):
            anchor = best
        return anchor.start_point[0] + 1, anchor.end_point[0] + 1, True, is_method
    for child in root.children:
        if child.start_point[0] + 1 <= line <= child.end_point[0] + 1:
            return child.start_point[0] + 1, child.end_point[0] + 1, False, False
    raise FixError("Couldn't find the code around that line.")


def _lang_name(path: str) -> str:
    return "typescript" if path.lower().endswith((".ts", ".tsx")) else "javascript"


def build_prompt(finding: Finding, snippet: str, lang: str, feedback: str | None = None) -> str:
    retry = (f"\nYour PREVIOUS attempt was rejected. Reason:\n{feedback}\nFix that problem this time.\n" if feedback else "")
    return f"""You are fixing ONE specific problem in {lang} code.

Problem ({finding.rule_id}): {finding.title}
Why it is a problem: {finding.explanation}
Direction for the fix: {finding.suggestion}
The offending line is: {finding.code}
{retry}
Code:
```{lang}
{snippet}
```

Rules:
- Output ONLY the full corrected code in one ```{lang} block. No explanation.
- Change as little as possible. Keep the function name, parameters and everything unrelated exactly the same.
- Do NOT write import or require statements. If you use `crypto`, `execFile` (from child_process) or `DOMPurify`, assume they are already imported.
- Do NOT remove the functionality; fix the problem while keeping the code useful.
"""


def _extract_code(reply: str) -> str:
    m = re.search(r"```(?:javascript|js|typescript|ts|tsx|jsx)?\s*\n(.*?)```", reply, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    return re.sub(r"^```\w*\s*\n", "", reply.strip()).strip("\n")


def _check_wrapped(snippet: str, path: str, is_method: bool) -> tuple[str, str]:
    """Text to parse for a standalone check (a method needs a class around it)."""
    return (f"class __Wrap {{\n{snippet}\n}}\n", path) if is_method else (snippet + "\n", path)


def _signature(snippet: str, path: str, is_method: bool):
    text, _ = _check_wrapped(snippet, path, is_method)
    suffix = Path(path).suffix or ".js"
    with tempfile.TemporaryDirectory(prefix="aice_jsfix_") as tmp:
        p = Path(tmp) / f"snippet{suffix}"
        p.write_text(text, encoding="utf-8")
        parsed = parse_js_file(str(p))
    if parsed.parse_errors or not parsed.functions:
        return None
    first = parsed.functions[0]
    return first.name, first.parameters, len(parsed.functions)


def _finding_counts(source: str, path: str) -> Counter:
    suffix = Path(path).suffix or ".js"
    with tempfile.TemporaryDirectory(prefix="aice_jsfix_") as tmp:
        p = Path(tmp) / f"check{suffix}"
        p.write_text(source, encoding="utf-8")
        return Counter(f.rule_id for f in scan_js_file(str(p), f"check{suffix}"))


def _has_syntax_error(source: str, path: str) -> bool:
    return get_js_parser(path).parse(source.encode("utf-8")).root_node.has_error


def verify_js_fix(old_source: str, new_source: str, finding: Finding, path: str) -> list[str]:
    """FAILED checks (empty = all good)."""
    if _has_syntax_error(new_source, path) and not _has_syntax_error(old_source, path):
        return ["The patched file is not valid JavaScript/TypeScript."]
    failures = []
    before, after = _finding_counts(old_source, path), _finding_counts(new_source, path)
    if after[finding.rule_id] >= before[finding.rule_id]:
        failures.append(f"The {finding.rule_id} problem is still detected after the change.")
    new_problems = [r for r in after if r != finding.rule_id and after[r] > before.get(r, 0)]
    if new_problems:
        failures.append("The change introduced new findings: " + ", ".join(sorted(new_problems)))
    return failures


_NEEDS_IMPORT = {
    "execFile": "const { execFile } = require('child_process')",
    "spawn": "const { spawn } = require('child_process')",
    "crypto": "const crypto = require('crypto')",
    "DOMPurify": "import DOMPurify from 'dompurify'",
}


# --- public entry point -----------------------------------------------------

def propose_js_fix(project_root: str, rel_path: str, line: int, rule_id: str, llm=None,
                   feedback: str | None = None) -> FixProposal:
    path = _safe_path(project_root, rel_path)
    old_source = path.read_text(encoding="utf-8", errors="replace")
    finding = None
    for f in scan_js_file(str(path), rel_path):
        if f.line == line and f.rule_id == rule_id:
            finding = f
            break
    if finding is None:
        raise FixError(f"No {rule_id} finding at line {line} of {rel_path}. "
                       "Run the scan again — the file may have changed.")
    trailing_nl = old_source.endswith("\n")

    # 1) deterministic fix
    fixer = _RULE_FIXERS.get(rule_id)
    if fixer:
        result = fixer(old_source, str(path), finding)
        if result:
            new_source, why, warnings = result
            failures = verify_js_fix(old_source, new_source, finding, str(path))
            return FixProposal(
                rule_id=rule_id, line=line, method="rule",
                diff=make_diff(rel_path, old_source, new_source),
                explanation=why, verified=not failures,
                checks=failures or ["Patched file parses.", "Original finding is gone.",
                                    "No new findings introduced."],
                warnings=warnings, new_source=new_source,
            )
        # not the shape the rule handles (multi-line, template string...): fall through to the model

    # 2) model fix of the enclosing scope only
    if llm is None:
        from app.core.llm_client import generate as llm

    start, end, is_function, is_method = find_js_scope(old_source, str(path), line)
    old_lines = old_source.splitlines()
    snippet = "\n".join(old_lines[start - 1:end])
    indent = re.match(r"\s*", old_lines[start - 1]).group(0)
    lang = _lang_name(rel_path)

    reply = llm(build_prompt(finding, textwrap.dedent(snippet), lang, feedback), timeout=180.0)
    new_snippet = _extract_code(reply)
    if not new_snippet.strip():
        raise FixError("The model returned an empty reply. Try again.")
    new_snippet = textwrap.indent(textwrap.dedent(new_snippet), indent)

    checks_failed = []
    if is_function:
        old_sig = _signature(textwrap.dedent(snippet), str(path), is_method)
        new_sig = _signature(textwrap.dedent(new_snippet), str(path), is_method)
        if new_sig is None:
            checks_failed.append("The model's reply isn't valid code for a single function; rejected.")
        elif old_sig is not None and old_sig[:2] != new_sig[:2]:
            checks_failed.append(
                f"The model changed the function signature {old_sig[0]}({', '.join(old_sig[1])}) -> "
                f"{new_sig[0]}({', '.join(new_sig[1])}).")

    new_lines = old_lines[:start - 1] + new_snippet.splitlines() + old_lines[end:]
    new_source = "\n".join(new_lines) + ("\n" if trailing_nl else "")
    warnings = []
    for name, how in _NEEDS_IMPORT.items():
        if re.search(rf"\b{name}\b", new_snippet) and not re.search(rf"\b{name}\b", old_source):
            warnings.append(f"The fix uses `{name}`, which this file doesn't import yet. "
                            f"Add something like `{how}` at the top.")
    checks_failed += verify_js_fix(old_source, new_source, finding, str(path))
    warnings.append(
        "Generated by a small local model. Read the diff before applying: the scanner "
        "check proves the pattern is gone, not that the new code behaves correctly.")
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
