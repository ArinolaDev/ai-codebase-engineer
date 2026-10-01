import tempfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import (
    ingest_from_zip,
    ingest_from_git,
    get_project_path,
    IngestError,
)
from app.ingestion.file_walker import walk_project, summarize_languages

router = APIRouter()


class GitIngestRequest(BaseModel):
    repo_url: str
    branch: str | None = None


class ProjectSummary(BaseModel):
    project_id: str
    file_count: int
    languages: dict[str, int]


@router.post("/upload", response_model=ProjectSummary)
async def upload_project(file: UploadFile = File(...)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip uploads are supported right now")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        project_id = ingest_from_zip(tmp_path)
    except IngestError as e:
        raise HTTPException(400, str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return _build_summary(project_id)


@router.post("/from-git", response_model=ProjectSummary)
def clone_project(payload: GitIngestRequest):
    try:
        project_id = ingest_from_git(payload.repo_url, payload.branch)
    except IngestError as e:
        raise HTTPException(400, str(e))

    return _build_summary(project_id)


@router.get("/{project_id}/summary", response_model=ProjectSummary)
def get_summary(project_id: str):
    try:
        return _build_summary(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))


def _build_summary(project_id: str) -> ProjectSummary:
    project_path = get_project_path(project_id)
    files = walk_project(str(project_path))
    return ProjectSummary(
        project_id=project_id,
        file_count=len(files),
        languages=summarize_languages(files),
    )