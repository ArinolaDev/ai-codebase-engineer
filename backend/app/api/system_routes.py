"""Small endpoints the web UI needs: environment status and a list of known projects."""

import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.llm_client import OLLAMA_BASE_URL, DEFAULT_MODEL
from app.ingestion.file_walker import walk_project, summarize_languages
from app.ingestion.ingest_service import get_project_path, IngestError

router = APIRouter()           # mounted at /api/system
projects_router = APIRouter()  # mounted at /api/projects


@router.get("/status")
def system_status():
    """Is Ollama up, which models are installed, is a GitHub token configured."""
    ollama = {"running": False, "models": [], "default_model": DEFAULT_MODEL}
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=1.5)
        r.raise_for_status()
        ollama["running"] = True
        ollama["models"] = sorted(m.get("name", "") for m in r.json().get("models", []))
    except Exception:  # noqa: BLE001 - any failure just means "not reachable"
        pass
    return {"ollama": ollama, "github_configured": bool(settings.github_token),
            "env": settings.app_env}


@projects_router.get("")
def list_projects():
    """Projects that exist on disk (newest first). Lets the UI show them after a page reload."""
    base = Path(settings.data_dir)
    out = []
    if base.is_dir():
        for d in base.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            try:
                files = walk_project(str(d))
                langs = summarize_languages(files)
                out.append({"project_id": d.name, "file_count": len(files), "languages": langs,
                            "created": int(d.stat().st_mtime)})
            except Exception:  # noqa: BLE001 - skip unreadable folders
                continue
    out.sort(key=lambda p: p["created"], reverse=True)
    return out[:30]


MAX_FILE_BYTES = 400_000


@projects_router.get("/{project_id}/file")
def read_project_file(project_id: str, path: str):
    """Read one source file (for the code viewer). Read-only; must be inside the project."""
    try:
        root = get_project_path(project_id).resolve()
    except IngestError as e:
        raise HTTPException(404, str(e))
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, f"File not found in project: {path}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(413, "File is too large to display.")
    return {"path": path.replace("\\", "/"),
            "content": target.read_text(encoding="utf-8", errors="replace")}


@projects_router.get("/{project_id}/files")
def list_project_files(project_id: str):
    """Flat list of source files with language, for pickers."""
    try:
        root = get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))
    return [{"path": f.path, "language": f.language} for f in walk_project(str(root))]
