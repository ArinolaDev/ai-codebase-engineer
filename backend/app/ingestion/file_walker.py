"""
Walks a project directory and returns a list of source files with detected
languages. Skips common junk directories and binary/irrelevant files so we
don't waste time parsing node_modules, .git internals, compiled artifacts, etc.
"""

from pathlib import Path
from dataclasses import dataclass

# Directories we never want to walk into, regardless of .gitignore
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
    "target", "vendor", ".idea", ".vscode", "coverage",
}

# Extension -> language name. We'll grow this as we add tree-sitter grammars.
EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
}


@dataclass
class SourceFile:
    path: str          # path relative to project root
    absolute_path: str
    language: str
    size_bytes: int


def detect_language(file_path: Path) -> str | None:
    return EXTENSION_LANGUAGE_MAP.get(file_path.suffix.lower())


def walk_project(root_dir: str) -> list[SourceFile]:
    root = Path(root_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Project root does not exist: {root_dir}")

    source_files: list[SourceFile] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip anything inside a directory we don't care about
        if any(part in SKIP_DIRS for part in path.parts):
            continue

        language = detect_language(path)
        if language is None:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue

        # Skip suspiciously large files (likely generated/minified/vendored)
        if size > 2_000_000:  # 2 MB
            continue

        source_files.append(
            SourceFile(
                path=str(path.relative_to(root)),
                absolute_path=str(path),
                language=language,
                size_bytes=size,
            )
        )

    return source_files


def summarize_languages(files: list[SourceFile]) -> dict[str, int]:
    """Quick language -> file count breakdown, useful for the codebase map."""
    summary: dict[str, int] = {}
    for f in files:
        summary[f.language] = summary.get(f.language, 0) + 1
    return summary