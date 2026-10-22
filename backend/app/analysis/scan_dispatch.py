"""One entry point for scanning a file in any supported language."""

from app.analysis.pattern_scanner import Finding, scan_python_file
from app.analysis.js_scanner import scan_js_file

SCAN_LANGUAGES = {"python", "javascript", "typescript"}


def scan_file(absolute_path: str, rel_path: str, language: str) -> list[Finding]:
    if language == "python":
        return scan_python_file(absolute_path, rel_path)
    if language in ("javascript", "typescript"):
        return scan_js_file(absolute_path, rel_path)
    return []
