"""
Turns parsed symbols (functions, classes) into text chunks for embedding.
We chunk by function/class rather than fixed token windows — this matters a
lot for code, since a fixed window can split a function in half and lose
all its meaning. One chunk = one coherent unit of code.

Code that sits OUTSIDE any function or class (constants, configuration, keys,
imports, top-level scripts) is gathered into "module level" chunks, so a question
like "where is the database password set?" can find it. Without them that code
was invisible to search and chat.
"""

from dataclasses import dataclass
from pathlib import Path

from app.parsing.python_parser import ParsedFile


@dataclass
class CodeChunk:
    chunk_id: str          # unique id: "<file_path>::<symbol_name>"
    file_path: str
    symbol_name: str
    symbol_type: str       # "function", "class" or "module" (top-level code)
    start_line: int
    end_line: int
    text: str               # the actual text we embed (source + metadata)


def _read_lines(file_path: str, start_line: int, end_line: int) -> str:
    lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
    # start_line/end_line are 1-indexed and inclusive
    snippet = lines[start_line - 1:end_line]
    return "\n".join(snippet)


_MODULE_CHUNK_CHARS = 1200   # small enough to reach the chat model whole (see CHAT_SNIPPET_CHARS)


def _module_level_chunks(parsed: ParsedFile, absolute_path: str) -> list[CodeChunk]:
    """Every non-blank line that is not inside a function or class, grouped into chunks.
    Lines are prefixed with their number so answers can cite exact lines."""
    try:
        lines = Path(absolute_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    covered: set[int] = set()
    for sym in [*parsed.functions, *parsed.classes]:
        covered.update(range(sym.start_line, sym.end_line + 1))

    chunks: list[CodeChunk] = []
    buf: list[tuple[int, str]] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        first, last = buf[0][0], buf[-1][0]
        body = "\n".join(f"{n}: {t}" for n, t in buf)
        header = (f"Module-level code in {parsed.path}, lines {first}-{last} "
                  f"(top-level statements outside any function or class: constants, configuration, "
                  f"settings, credentials, imports)")
        chunks.append(CodeChunk(
            chunk_id=f"{parsed.path}::module#{len(chunks)}",
            file_path=parsed.path,
            symbol_name="module level",
            symbol_type="module",
            start_line=first,
            end_line=last,
            text=f"{header}\n\n{body}",
        ))
        buf, size = [], 0

    for n, text in enumerate(lines, start=1):
        if n in covered or not text.strip():
            continue
        buf.append((n, text))
        size += len(text) + 8
        if size >= _MODULE_CHUNK_CHARS:
            flush()
    flush()
    return chunks


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

    chunks.extend(_module_level_chunks(parsed, absolute_path))
    return chunks
