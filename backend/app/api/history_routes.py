from fastapi import APIRouter, HTTPException

from app.analysis import history
from app.ingestion.ingest_service import IngestError, get_project_path

router = APIRouter()


def _check(project_id: str) -> None:
    try:
        get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))


@router.get("/{project_id}/history")
def scan_history(project_id: str):
    """Every saved scan for the project, newest first, with what changed since the one before it."""
    _check(project_id)
    return history.list_scans(project_id)


@router.get("/{project_id}/history/{scan_id}")
def scan_detail(project_id: str, scan_id: int):
    """One saved scan: the findings that are new and the ones that were fixed since the scan before it."""
    _check(project_id)
    d = history.get_scan(project_id, scan_id)
    if d is None:
        raise HTTPException(404, "That scan isn't in the history.")
    return d


@router.delete("/{project_id}/history")
def clear_history(project_id: str):
    _check(project_id)
    history.clear(project_id)
    return {"cleared": True}
