"""
Handles getting a codebase onto disk, either by extracting an uploaded zip
or cloning a git repository. Both paths converge on the same result: a
project_id and a directory under DATA_DIR we can then walk/parse/index.
"""

import shutil
import uuid
import zipfile
from pathlib import Path

import git

from app.core.config import settings


class IngestError(Exception):
    pass


def _project_dir(project_id: str) -> Path:
    return Path(settings.data_dir) / project_id


def create_project_id() -> str:
    return uuid.uuid4().hex[:12]


def ingest_from_zip(zip_path: str) -> str:
    """Extract an uploaded zip into a fresh project directory. Returns project_id."""
    project_id = create_project_id()
    target_dir = _project_dir(project_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
    except zipfile.BadZipFile as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Uploaded file is not a valid zip: {e}")

    # If the zip contained a single top-level folder, flatten it so
    # target_dir IS the project root rather than target_dir/<folder>/
    entries = list(target_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(target_dir / item.name))
        inner.rmdir()

    return project_id


def ingest_from_git(repo_url: str, branch: str | None = None) -> str:
    """Clone a git repository into a fresh project directory. Returns project_id."""
    project_id = create_project_id()
    target_dir = _project_dir(project_id)

    try:
        clone_kwargs = {"depth": 1}
        if branch:
            clone_kwargs["branch"] = branch
        git.Repo.clone_from(repo_url, target_dir, **clone_kwargs)
    except git.GitCommandError as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Failed to clone repository: {e}")

    return project_id


def get_project_path(project_id: str) -> Path:
    path = _project_dir(project_id)
    if not path.exists():
        raise IngestError(f"No project found with id {project_id}")
    return path


def delete_project(project_id: str) -> None:
    shutil.rmtree(_project_dir(project_id), ignore_errors=True)