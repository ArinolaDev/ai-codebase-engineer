"""
Deterministic bug / security scanner for JavaScript and TypeScript, using tree-sitter.

Same idea as the Python scanner: every finding comes from an explicit rule, so results are
reproducible and free. Rule ids match the Python scanner where the problem is the same
(SEC001 key formats, SEC002 hardcoded secret, SEC003 SQL, SEC004 eval, SEC005 shell,
SEC007 weak hash, SEC008 TLS) and add JS-only ones (SEC010 XSS sinks, SEC011 weak randomness,
BUG003 loose equality, BUG004 empty catch, BUG005 debugger).
"""

import re
from pathlib import Path

from app.analysis.pattern_scanner import (
    Finding, _KEY_PATTERNS, _PLACEHOLDER, _SECRET_NAME,
)
from app.parsing.js_parser import get_js_parser

JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")

_SQL_WORDS = re.compile(r"\b(select|insert\s+into|update|delete\s+from|drop\s+table|create\s+table|alter\s+table)\b", re.IGNORECASE)
_SQL_METHODS = {"query", "execute", "raw", "run", "all", "get", "exec", "prepare", "$queryRawUnsafe", "$executeRawUnsafe"}
_RANDOM_CONTEXT = re.compile(r"(token|secret|password|passwd|session|nonce|salt|api_?key|csrf|otp|reset)", re.IGNORECASE)
_URLISH = re.compile(r"^(https?://|/|\./|\.\./)")


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _is_plain_string(node) -> bool:
    if node.type == "string":
        return True
    if node.type == "template_string":
        return not any(c.type == "template_substitution" for c in node.children)
    return False


def _string_value(node, src: bytes) -> str:
    text = _txt(node, src)
    return text[1:-1] if len(text) >= 2 and text[0] in "'\"`" else text


def _is_dynamic(node) -> bool:
    """Built at runtime from something other than a fixed string."""
    if node is None:
        return False
    if _is_plain_string(node):
        return False
    if node.type in ("template_string", "binary_expression", "identifier", "member_expression",
                     "call_expression", "subscript_expression", "await_expression"):
        return True
    if node.type == "parenthesized_expression" and node.named_child_count:
        return _is_dynamic(node.named_children[0])
    return False


def _leftmost_string(node, src: bytes) -> str:
    while node is not None and node.type == "binary_expression":
        node = node.child_by_field_name("left")
    return _txt(node, src) if node is not None and node.type in ("string", "template_string") else ""


def scan_js_file(absolute_path: str, rel_path: str) -> list[Finding]:
    low = absolute_path.lower()
    if low.endswith(".min.js") or low.endswith(".d.ts"):
        return []   # generated / declaration files: nothing to act on
    try:
        src = Path(absolute_path).read_bytes()
        root = get_js_parser(absolute_path).parse(src).root_node
    except (OSError, ValueError):
        return []

    text = src.decode("utf-8", errors="replace")
    lines = text.splitlines()
    uses_child_process = "child_process" in text
    findings: list[Finding] = []

    def add(node_or_line, rule_id, severity, title, explanation, suggestion):
        line = node_or_line if isinstance(node_or_line, int) else node_or_line.start_point[0] + 1
        code = lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""
        findings.append(Finding(rel_path, line, rule_id, severity, title, explanation, suggestion, code))

    # 1) Known credential formats anywhere in the text
    for i, line_text in enumerate(lines, start=1):
        for label, pattern in _KEY_PATTERNS:
            if pattern.search(line_text):
                add(i, "SEC001", "high", f"Hardcoded credential: {label}",
                    "A string matching a known credential format is committed in source. "
                    "Anyone with repo access (or a leaked copy) can use it.",
                    "Remove it, rotate the credential, and load it from an environment "
                    "variable (process.env) or a secrets manager.")

    def secret_literal(name: str, value_node, report_node):
        if not name or value_node is None or not _is_plain_string(value_node):
            return
        value = _string_value(value_node, src)
        if (_SECRET_NAME.search(name) and len(value) >= 6 and not _PLACEHOLDER.search(value)
                and not _URLISH.search(value)):
            add(report_node, "SEC002", "high", f"Hardcoded secret in `{name}`",
                "A variable or property named like a secret is assigned a literal string. "
                "Secrets in source end up in version control history.",
                f"Use `process.env.{re.sub(r'[^A-Za-z0-9]+', '_', name).upper()}` (or your config "
                "system) and keep the real value out of the repo.")

    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        t = node.type

        if t == "call_expression":
            fn = node.child_by_field_name("function")
            fn_text = _txt(fn, src) if fn is not None else ""
            args_node = node.child_by_field_name("arguments")
            args = [a for a in (args_node.named_children if args_node is not None else []) if a.type != "comment"]
            first = args[0] if args else None
            last_name = fn_text.split(".")[-1]

            if fn_text == "eval":
                add(node, "SEC004", "high", "Use of `eval()`",
                    "eval runs a string as code. If any part of it can be influenced by a user, "
                    "they can run their own code in your app.",
                    "Remove eval. Parse data with JSON.parse, or call the function you actually need.")
            elif fn_text in ("setTimeout", "setInterval") and first is not None and first.type in ("string", "template_string"):
                add(node, "SEC004", "medium", f"`{fn_text}` with a string argument",
                    "Passing a string makes the engine evaluate it like eval.",
                    "Pass a function instead: `setTimeout(() => work(), 1000)`.")
            elif uses_child_process and last_name in ("exec", "execSync") and _is_dynamic(first):
                add(node, "SEC005", "high", f"Shell command built at runtime ({last_name})",
                    "exec runs its argument in a shell. If a user can influence any part of the "
                    "command, they can run their own commands.",
                    "Use execFile / spawn with an argument array and no shell, and validate the inputs.")
            elif last_name in _SQL_METHODS and first is not None:
                is_sql = False
                if first.type == "template_string" and not _is_plain_string(first):
                    is_sql = bool(_SQL_WORDS.search(_txt(first, src)))
                elif first.type == "binary_expression":
                    is_sql = bool(_SQL_WORDS.search(_leftmost_string(first, src)))
                if is_sql:
                    add(node, "SEC003", "high", "Possible SQL injection",
                        "The query text is built by joining strings, so a crafted input can change the query.",
                        "Use placeholders with parameters, e.g. `db.query('SELECT * FROM users WHERE id = $1', [id])`.")
            elif last_name == "createHash" and first is not None and first.type == "string" \
                    and _string_value(first, src).lower() in ("md5", "sha1"):
                add(node, "SEC007", "medium", f"Weak hash: {_string_value(first, src).lower()}",
                    "MD5 and SHA-1 are broken for security use (collisions, fast brute force).",
                    "Use sha256 or better for integrity, and bcrypt/scrypt/argon2 for passwords.")
            elif fn_text in ("document.write", "document.writeln") and _is_dynamic(first):
                add(node, "SEC010", "medium", f"`{fn_text}` with dynamic content",
                    "Writing built-up strings into the page lets injected markup run as script (XSS).",
                    "Build elements with createElement and set textContent instead.")
            elif fn_text == "Math.random":
                stmt = node
                while stmt.parent is not None and stmt.type not in (
                        "lexical_declaration", "variable_declaration", "expression_statement",
                        "return_statement", "function_declaration", "method_definition", "pair"):
                    stmt = stmt.parent
                context = _txt(stmt, src)[:300] if stmt.type not in ("function_declaration", "method_definition") \
                    else _txt(stmt.child_by_field_name("name") or stmt, src)
                if _RANDOM_CONTEXT.search(context):
                    add(node, "SEC011", "medium", "Math.random used for something secret",
                        "Math.random is predictable, so tokens or ids made from it can be guessed.",
                        "Use crypto.randomUUID() or crypto.randomBytes(n) (Node), or crypto.getRandomValues (browser).")

        elif t == "new_expression":
            ctor = node.child_by_field_name("constructor")
            if ctor is not None and _txt(ctor, src) == "Function":
                add(node, "SEC004", "high", "Use of `new Function()`",
                    "new Function compiles a string into code, which is eval by another name.",
                    "Write the function normally. If you need dynamic behavior, pick from a fixed set of functions.")

        elif t == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None and right is not None:
                left_text = _txt(left, src)
                prop = left.child_by_field_name("property") if left.type == "member_expression" else None
                prop_name = _txt(prop, src) if prop is not None else (left_text if left.type == "identifier" else "")
                if prop_name in ("innerHTML", "outerHTML") and not _is_plain_string(right):
                    add(node, "SEC010", "medium", f"Assigning to `{prop_name}` from a variable",
                        "If the value contains user input, injected markup runs as script (XSS).",
                        "Use textContent for text, or sanitize the HTML (for example with DOMPurify) first.")
                elif left_text.endswith("NODE_TLS_REJECT_UNAUTHORIZED") and _is_plain_string(right) \
                        and _string_value(right, src) == "0":
                    add(node, "SEC008", "medium", "TLS certificate verification disabled",
                        "Turning this off lets anyone on the network impersonate the server you connect to.",
                        "Remove it. If you use a private CA, add that certificate instead of disabling checks.")
                else:
                    secret_literal(prop_name, right, node)

        elif t == "variable_declarator":
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.type == "identifier":
                secret_literal(_txt(name_node, src), node.child_by_field_name("value"), node)

        elif t in ("pair", "public_field_definition", "field_definition"):
            key = node.child_by_field_name("key") or node.child_by_field_name("name") or node.child_by_field_name("property")
            value = node.child_by_field_name("value")
            if key is not None and value is not None:
                key_text = _txt(key, src).strip("'\"`")
                if key_text == "rejectUnauthorized" and _txt(value, src) == "false":
                    add(node, "SEC008", "medium", "TLS certificate verification disabled",
                        "Turning this off lets anyone on the network impersonate the server you connect to.",
                        "Remove it. If you use a private CA, add that certificate instead of disabling checks.")
                else:
                    secret_literal(key_text, value, node)

        elif t == "jsx_attribute":
            first = node.children[0] if node.children else None
            if first is not None and _txt(first, src) == "dangerouslySetInnerHTML":
                add(node, "SEC010", "medium", "`dangerouslySetInnerHTML` used",
                    "React will insert this HTML as is. If it contains user input, injected markup runs as script (XSS).",
                    "Render text normally, or sanitize the HTML (for example with DOMPurify) first.")

        elif t == "binary_expression":
            op = node.child_by_field_name("operator")
            if op is not None and _txt(op, src) in ("==", "!="):
                l, r = node.child_by_field_name("left"), node.child_by_field_name("right")
                idiom = any(x is not None and (x.type in ("null", "undefined") or _txt(x, src) == "undefined")
                            for x in (l, r))   # `x == null` is the standard "null or undefined" check
                if not idiom:
                    add(node, "BUG003", "low", f"Loose equality `{_txt(op, src)}`",
                        "`==` converts types before comparing, so `0 == ''` and `'1' == 1` are true. "
                        "That hides bugs.",
                        f"Use `{'===' if _txt(op, src) == '==' else '!=='}` unless you really want type conversion.")

        elif t == "catch_clause":
            body = node.child_by_field_name("body")
            if body is not None and body.type == "statement_block" and body.named_child_count == 0:
                add(node, "BUG004", "low", "Empty `catch` block",
                    "The error is swallowed, so failures go unnoticed and are hard to debug.",
                    "Log the error, handle it, or rethrow. If ignoring it is intended, say why in a comment.")

        elif t == "debugger_statement":
            add(node, "BUG005", "low", "`debugger` statement left in",
                "It pauses execution for anyone with dev tools open.",
                "Remove it before shipping.")

    # one finding per rule per line is enough
    unique, seen = [], set()
    for f in findings:
        key = (f.line, f.rule_id)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
