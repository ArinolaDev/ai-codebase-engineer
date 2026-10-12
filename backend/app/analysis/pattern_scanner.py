"""
Deterministic bug / security pattern scanner for Python files.

No LLM involved: every finding comes from an explicit rule, so results are
reproducible and cheap. The LLM pass (later in Phase 5) will sit on top of
this to catch the things rules can't — logic bugs, bad error handling, etc.

Uses Python's stdlib `ast` module (exact for Python). tree-sitter remains
our cross-language layer; when we add JS/Java rules they'll use that.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Finding:
    file_path: str
    line: int
    rule_id: str
    severity: str  # "high" | "medium" | "low"
    title: str
    explanation: str
    suggestion: str
    code: str  # the offending source line


# --- helpers ---------------------------------------------------------------

_SECRET_NAME = re.compile(
    r"(password|passwd|pwd|secret|api_?key|access_?key|auth_?token|token|private_?key)",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(
    r"(your_|<.*>|changeme|change_me|example|xxx+|placeholder|dummy|test|todo|\*{3,})",
    re.IGNORECASE,
)
# Well-known credential formats, matched anywhere in a line
_KEY_PATTERNS = [
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub personal access token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("Secret key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def _dotted(node: ast.AST) -> str:
    """Turn `a.b.c` attribute chains into the string 'a.b.c'."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_dynamic_string(node: ast.AST) -> bool:
    """True if the expression builds a string from variables
    (f-string, concatenation, % formatting, or .format())."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        # "literal" + var  or  "literal %s" % var
        return not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and isinstance(node.func.value, ast.Constant)
    return False


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


# --- the scanner -----------------------------------------------------------

def scan_python_file(absolute_path: str, rel_path: str) -> list[Finding]:
    try:
        source = Path(absolute_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, ValueError, OSError):
        return []  # unparseable file: skip rather than crash the whole scan

    lines = source.splitlines()
    findings: list[Finding] = []

    def add(node_or_line, rule_id, severity, title, explanation, suggestion):
        line = node_or_line if isinstance(node_or_line, int) else node_or_line.lineno
        code = lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""
        findings.append(
            Finding(rel_path, line, rule_id, severity, title, explanation, suggestion, code)
        )

    # 1) Known credential formats anywhere in the text
    for i, text in enumerate(lines, start=1):
        for label, pattern in _KEY_PATTERNS:
            if pattern.search(text):
                add(
                    i, "SEC001", "high", f"Hardcoded credential: {label}",
                    "A string matching a known credential format is committed in source. "
                    "Anyone with repo access (or a leaked copy) can use it.",
                    "Remove it, rotate the credential, and load it from an environment "
                    "variable or secrets manager.",
                )

    for node in ast.walk(tree):
        # 2) Secret-looking variable assigned a string literal
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                value = node.value.value
                for target in node.targets:
                    name = target.id if isinstance(target, ast.Name) else (
                        target.attr if isinstance(target, ast.Attribute) else ""
                    )
                    if (
                        name
                        and _SECRET_NAME.search(name)
                        and len(value) >= 6
                        and not _PLACEHOLDER.search(value)
                    ):
                        add(
                            node, "SEC002", "high", f"Hardcoded secret in `{name}`",
                            "A variable named like a secret is assigned a literal string. "
                            "Secrets in source end up in version control history.",
                            f'Use `{name} = os.environ["{name.upper()}"]` (or your config '
                            "system) and keep the real value out of the repo.",
                        )

        if isinstance(node, ast.Call):
            fn = _dotted(node.func)
            last = fn.split(".")[-1] if fn else ""

            # 3) SQL built from dynamic strings
            if last in {"execute", "executemany", "executescript", "raw"} and node.args:
                if _is_dynamic_string(node.args[0]):
                    add(
                        node, "SEC003", "high", "Possible SQL injection",
                        "The query string is built with an f-string, concatenation, "
                        "% or .format(), so user-controlled input could change the "
                        "SQL itself.",
                        'Use parameterized queries, e.g. cursor.execute("... WHERE id = ?", (user_id,)) '
                        "(or %s placeholders for psycopg2).",
                    )

            # 4) eval / exec
            if fn in {"eval", "exec"}:
                add(
                    node, "SEC004", "high", f"Use of `{fn}()`",
                    f"`{fn}` runs arbitrary code. If any part of the input is "
                    "user-controlled, this is remote code execution.",
                    "Use ast.literal_eval for data, or a dispatch table of allowed functions.",
                )

            # 5) os.system / shell=True
            if fn == "os.system":
                add(
                    node, "SEC005", "high", "Shell command via os.system",
                    "os.system passes the string to a shell, enabling command injection "
                    "if any part is user-controlled.",
                    "Use subprocess.run([...], shell=False) with an argument list.",
                )
            if fn.startswith("subprocess.") and _kw(node, "shell") is not None:
                shell = _kw(node, "shell")
                if isinstance(shell, ast.Constant) and shell.value is True:
                    add(
                        node, "SEC005", "high", "subprocess call with shell=True",
                        "shell=True runs the command through a shell, enabling command "
                        "injection if any part is user-controlled.",
                        "Pass an argument list and drop shell=True.",
                    )

            # 6) Unsafe deserialization
            if fn in {"pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads"}:
                add(
                    node, "SEC006", "high", "Unsafe deserialization with pickle",
                    "Unpickling untrusted data can execute arbitrary code.",
                    "Use JSON (or another data-only format) for untrusted input.",
                )
            if fn == "yaml.load" and _kw(node, "Loader") is None and len(node.args) < 2:
                add(
                    node, "SEC006", "high", "yaml.load without a safe Loader",
                    "The default loader can construct arbitrary Python objects from YAML.",
                    "Use yaml.safe_load(...).",
                )

            # 7) Weak hashes
            if fn in {"hashlib.md5", "hashlib.sha1"}:
                add(
                    node, "SEC007", "medium", f"Weak hash: {fn.split('.')[-1]}",
                    "MD5/SHA-1 are broken for security purposes (collisions, fast to brute-force).",
                    "Use hashlib.sha256 for integrity checks, and bcrypt/argon2 for passwords.",
                )

            # 8) TLS verification disabled
            if fn.startswith("requests.") or last in {"get", "post", "put", "delete", "request"}:
                verify = _kw(node, "verify")
                if isinstance(verify, ast.Constant) and verify.value is False:
                    add(
                        node, "SEC008", "medium", "TLS certificate verification disabled",
                        "verify=False makes the connection vulnerable to man-in-the-middle attacks.",
                        "Remove verify=False; if you use a private CA, point verify= at its bundle.",
                    )

            # 9) Debug mode on
            if last == "run":
                debug = _kw(node, "debug")
                if isinstance(debug, ast.Constant) and debug.value is True:
                    add(
                        node, "SEC009", "medium", "Debug mode enabled",
                        "Flask/Werkzeug debug mode exposes an interactive debugger that "
                        "allows code execution if reachable.",
                        "Drive debug from an environment variable and keep it off in production.",
                    )

        # 10) Bare except swallows everything, including KeyboardInterrupt
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            add(
                node, "BUG001", "low", "Bare `except:`",
                "A bare except catches everything (including KeyboardInterrupt/SystemExit) "
                "and hides real bugs.",
                "Catch the specific exceptions you expect, e.g. `except ValueError:`.",
            )

        # 11) Mutable default arguments
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
            for d in defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    add(
                        d, "BUG002", "medium", f"Mutable default argument in `{node.name}`",
                        "Default values are created once at definition time, so the same "
                        "list/dict is shared across every call — a classic source of "
                        "state leaking between calls.",
                        "Use `None` as the default and create the list/dict inside the function.",
                    )

    return findings


def sort_findings(findings: list[Finding]) -> list[Finding]:
    # De-dupe: if a known-credential-format hit (SEC001) and a secret-variable
    # hit (SEC002) land on the same line, they're the same problem — keep SEC001.
    sec001_lines = {(f.file_path, f.line) for f in findings if f.rule_id == "SEC001"}
    deduped = [
        f for f in findings
        if not (f.rule_id == "SEC002" and (f.file_path, f.line) in sec001_lines)
    ]
    return sorted(deduped, key=lambda f: (SEVERITY_ORDER[f.severity], f.file_path, f.line))