"""
Parses a single Python file with tree-sitter and extracts a normalized list
of symbols: functions, classes, and imports. This is the foundation for
everything downstream — bug detection, test generation, and codebase chat
all need to know "what functions/classes exist and where."
"""

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter_languages import get_parser

_parser = get_parser("python")


@dataclass
class FunctionSymbol:
    name: str
    start_line: int
    end_line: int
    parameters: list[str]
    docstring: str | None
    parent_class: str | None = None  # None if it's a top-level function


@dataclass
class ClassSymbol:
    name: str
    start_line: int
    end_line: int
    docstring: str | None
    methods: list[str] = field(default_factory=list)


@dataclass
class ImportSymbol:
    module: str
    line: int


@dataclass
class ParsedFile:
    path: str
    functions: list[FunctionSymbol]
    classes: list[ClassSymbol]
    imports: list[ImportSymbol]
    parse_errors: bool  # True if tree-sitter hit syntax it couldn't parse cleanly


def _get_docstring(node, source: bytes) -> str | None:
    """Look for a string expression as the first statement in a body (the
    tree-sitter-idiomatic way to detect a Python docstring)."""
    body = node.child_by_field_name("body")
    if body is None or body.child_count == 0:
        return None
    first = body.children[0]
    if first.type == "expression_statement" and first.child_count > 0:
        expr = first.children[0]
        if expr.type == "string":
            text = source[expr.start_byte:expr.end_byte].decode("utf-8", errors="replace")
            return text.strip("\"'").strip()
    return None


def _get_params(node, source: bytes) -> list[str]:
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []
    names = []
    for child in params_node.children:
        if child.type in ("identifier",):
            names.append(source[child.start_byte:child.end_byte].decode("utf-8"))
        elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            # First child of these wrapper nodes is the identifier
            ident = child.children[0] if child.child_count > 0 else None
            if ident is not None and ident.type == "identifier":
                names.append(source[ident.start_byte:ident.end_byte].decode("utf-8"))
    return names


def parse_python_file(file_path: str) -> ParsedFile:
    source_bytes = Path(file_path).read_bytes()
    tree = _parser.parse(source_bytes)
    root = tree.root_node

    functions: list[FunctionSymbol] = []
    classes: list[ClassSymbol] = []
    imports: list[ImportSymbol] = []

    def walk(node, current_class: str | None = None):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            fn = FunctionSymbol(
                name=name,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parameters=_get_params(node, source_bytes),
                docstring=_get_docstring(node, source_bytes),
                parent_class=current_class,
            )
            functions.append(fn)
            if current_class:
                for c in classes:
                    if c.name == current_class:
                        c.methods.append(name)
                        break
            # Still walk into the function body in case of nested functions
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    walk(child, current_class)
            return

        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            cls = ClassSymbol(
                name=name,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                docstring=_get_docstring(node, source_bytes),
            )
            classes.append(cls)
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    walk(child, current_class=name)
            return

        if node.type == "import_statement":
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
            imports.append(ImportSymbol(module=text.replace("import ", "").strip(), line=node.start_point[0] + 1))
            return

        if node.type == "import_from_statement":
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
            imports.append(ImportSymbol(module=text.strip(), line=node.start_point[0] + 1))
            return

        for child in node.children:
            walk(child, current_class)

    walk(root)

    return ParsedFile(
        path=file_path,
        functions=functions,
        classes=classes,
        imports=imports,
        parse_errors=root.has_error,
    )