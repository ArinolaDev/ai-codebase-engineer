"""
Turns parsed symbols (functions, classes) into text chunks for embedding.
We chunk by function/class rather than fixed token windows — this matters a
lot for code, since a fixed window can split a function in half and lose
all its meaning. One chunk = one coherent unit of code.
"""

from dataclasses import dataclass
from pathlib import Path

from app.parsing.python_parser import ParsedFile


@dataclass
class CodeChunk:
    chunk_id: str          # unique id: "<file_path>::<symbol_name>"
    file_path: str
    symbol_name: str
    symbol_type: str       # "function" or "class"
    start_line: int
    end_line: int
    text: str               # the actual text we embed (source + metadata)


def _read_lines(file_path: str, start_line: int, end_line: int) -> str:
    lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
    # start_line/end_line are 1-indexed and inclusive
    snippet = lines[start_line - 1:end_line]
    return "\n".join(snippet)


def chunk_parsed_file(parsed: ParsedFile, absolute_path: str) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []

    for fn in parsed.functions:
        source = _read_lines(absolute_path, fn.start_line, fn.end_line)
        header = f"Function `{fn.name}` in {parsed.path}"
        if fn.parent_class:
            header = f"Method `{fn.name}` of class `{fn.parent_class}` in {parsed.path}"
        if fn.docstring:
            header += f"\nDocstring: {fn.docstring}"
        text = f"{header}\n\n{source}"

        chunks.append(
            CodeChunk(
                chunk_id=f"{parsed.path}::{fn.parent_class or ''}.{fn.name}",
                file_path=parsed.path,
                symbol_name=fn.name,
                symbol_type="function",
                start_line=fn.start_line,
                end_line=fn.end_line,
                text=text,
            )
        )

    for cls in parsed.classes:
        source = _read_lines(absolute_path, cls.start_line, cls.end_line)
        header = f"Class `{cls.name}` in {parsed.path}"
        if cls.docstring:
            header += f"\nDocstring: {cls.docstring}"
        if cls.methods:
            header += f"\nMethods: {', '.join(cls.methods)}"
        text = f"{header}\n\n{source[:2000]}"  # cap very large classes

        chunks.append(
            CodeChunk(
                chunk_id=f"{parsed.path}::{cls.name}",
                file_path=parsed.path,
                symbol_name=cls.name,
                symbol_type="class",
                start_line=cls.start_line,
                end_line=cls.end_line,
                text=text,
            )
        )

    return chunks