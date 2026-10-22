"""Pick the right parser for a file's language."""

from app.parsing.python_parser import ParsedFile, parse_python_file
from app.parsing.js_parser import parse_js_file

PARSE_LANGUAGES = {"python", "javascript", "typescript"}


def parse_file(absolute_path: str, language: str) -> ParsedFile:
    if language == "python":
        return parse_python_file(absolute_path)
    return parse_js_file(absolute_path)
