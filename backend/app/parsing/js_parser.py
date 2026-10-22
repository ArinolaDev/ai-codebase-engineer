"""
Parses JavaScript and TypeScript (.js .jsx .mjs .cjs .ts .tsx) with tree-sitter and returns
the same ParsedFile the Python parser returns, so indexing and chat treat every language alike.

Functions found: declarations, generators, methods, and arrow functions or function expressions
assigned to a name (`const add = (a, b) => a + b`, `exports.run = function () {}`, class fields).
"""

from pathlib import Path

from tree_sitter_languages import get_parser

from app.parsing.python_parser import (
    ClassSymbol, FunctionSymbol, ImportSymbol, ParsedFile,
)

_parsers: dict = {}


def grammar_for(file_path: str) -> str:
    """tsx files need their own grammar; plain .ts must not use it (angle-bracket casts)."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".tsx":
        return "tsx"
    if suffix == ".ts":
        return "typescript"
    return "javascript"   # .js .jsx .mjs .cjs (the JS grammar understands JSX)


def get_js_parser(file_path: str):
    g = grammar_for(file_path)
    if g not in _parsers:
        _parsers[g] = get_parser(g)
    return _parsers[g]


_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _params(node, src: bytes) -> list[str]:
    p = node.child_by_field_name("parameters")
    if p is None:   # arrow function with a single bare parameter: x => x * 2
        single = node.child_by_field_name("parameter")
        return [_txt(single, src)] if single is not None else []
    names = []
    for c in p.named_children:
        if c.type == "comment":
            continue
        target = c.child_by_field_name("pattern") or c.child_by_field_name("left") or c
        if target.type == "rest_pattern" and target.named_child_count:
            names.append("..." + _txt(target.named_children[0], src))
        else:
            names.append(_txt(target, src).split(":")[0].strip())
    return names


def _doc_comment(node, src: bytes) -> str | None:
    """A /** ... */ or // comment sitting right above the symbol."""
    anchor = node.parent if node.parent is not None and node.parent.type == "export_statement" else node
    prev = anchor.prev_sibling
    if prev is not None and prev.type == "comment" and prev.end_point[0] >= anchor.start_point[0] - 1:
        text = _txt(prev, src)
        text = text.strip().removeprefix("/**").removeprefix("/*").removeprefix("//").removesuffix("*/")
        lines = [ln.strip().lstrip("*").strip() for ln in text.splitlines()]
        return " ".join(ln for ln in lines if ln) or None
    return None


def _name_of_assignment_target(node, src: bytes) -> str | None:
    if node.type == "identifier":
        return _txt(node, src)
    if node.type == "member_expression":   # exports.run = ... / module.exports.run = ... / this.run = ...
        prop = node.child_by_field_name("property")
        return _txt(prop, src) if prop is not None else None
    return None


def parse_js_file(file_path: str) -> ParsedFile:
    src = Path(file_path).read_bytes()
    root = get_js_parser(file_path).parse(src).root_node

    functions: list[FunctionSymbol] = []
    classes: list[ClassSymbol] = []
    imports: list[ImportSymbol] = []

    def add_function(name: str, fn_node, anchor, parent_class):
        functions.append(FunctionSymbol(
            name=name,
            start_line=anchor.start_point[0] + 1,
            end_line=anchor.end_point[0] + 1,
            parameters=_params(fn_node, src),
            docstring=_doc_comment(anchor, src),
            parent_class=parent_class,
        ))
        if parent_class:
            for c in classes:
                if c.name == parent_class:
                    c.methods.append(name)
                    break

    def walk(node, cls: str | None = None):
        t = node.type

        if t in ("function_declaration", "generator_function_declaration"):
            n = node.child_by_field_name("name")
            if n is not None:
                add_function(_txt(n, src), node, node, cls)
            for c in node.children:
                walk(c, cls)
            return

        if t in ("class_declaration", "abstract_class_declaration", "class"):
            n = node.child_by_field_name("name")
            name = _txt(n, src) if n is not None else None
            if name:
                classes.append(ClassSymbol(
                    name=name, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    docstring=_doc_comment(node, src)))
            body = node.child_by_field_name("body")
            for c in (body.children if body is not None else []):
                walk(c, name or cls)
            return

        if t == "method_definition":
            n = node.child_by_field_name("name")
            if n is not None:
                add_function(_txt(n, src), node, node, cls)
            for c in node.children:
                walk(c, cls)
            return

        if t in ("public_field_definition", "field_definition"):
            n = node.child_by_field_name("name") or node.child_by_field_name("property")
            v = node.child_by_field_name("value")
            if n is not None and v is not None and v.type in _FUNCTION_VALUES:
                add_function(_txt(n, src), v, node, cls)
            for c in node.children:
                walk(c, cls)
            return

        if t == "variable_declarator":   # const add = (a, b) => ...
            n = node.child_by_field_name("name")
            v = node.child_by_field_name("value")
            if n is not None and n.type == "identifier" and v is not None and v.type in _FUNCTION_VALUES:
                anchor = node.parent if node.parent is not None and node.parent.type in (
                    "lexical_declaration", "variable_declaration") else node
                if anchor.parent is not None and anchor.parent.type == "export_statement":
                    anchor = anchor.parent
                add_function(_txt(n, src), v, anchor, cls)
            # a require() call here is an import too
            if v is not None and v.type == "call_expression":
                f = v.child_by_field_name("function")
                if f is not None and _txt(f, src) == "require":
                    imports.append(ImportSymbol(module=_txt(node.parent if node.parent is not None else node, src).strip(),
                                                line=node.start_point[0] + 1))
            for c in node.children:
                walk(c, cls)
            return

        if t == "assignment_expression":   # exports.run = function () {}
            l = node.child_by_field_name("left")
            r = node.child_by_field_name("right")
            if l is not None and r is not None and r.type in _FUNCTION_VALUES:
                name = _name_of_assignment_target(l, src)
                if name:
                    add_function(name, r, node.parent if node.parent is not None and node.parent.type == "expression_statement" else node, cls)
            for c in node.children:
                walk(c, cls)
            return

        if t == "import_statement":
            imports.append(ImportSymbol(module=_txt(node, src).strip(), line=node.start_point[0] + 1))
            return

        for c in node.children:
            walk(c, cls)

    walk(root)

    # A function that is only the value of another named function is counted once: drop exact duplicates.
    seen, unique = set(), []
    for f in functions:
        key = (f.name, f.start_line, f.parent_class)
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return ParsedFile(path=file_path, functions=unique, classes=classes, imports=imports,
                      parse_errors=root.has_error)
