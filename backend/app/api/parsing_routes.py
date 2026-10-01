from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.ingestion.file_walker import walk_project
from app.parsing.python_parser import parse_python_file

router = APIRouter()


class FunctionOut(BaseModel):
    name: str
    start_line: int
    end_line: int
    parameters: list[str]
    docstring: str | None
    parent_class: str | None


class ClassOut(BaseModel):
    name: str
    start_line: int
    end_line: int
    docstring: str | None
    methods: list[str]


class ImportOut(BaseModel):
    module: str
    line: int


class FileParseOut(BaseModel):
    path: str
    functions: list[FunctionOut]
    classes: list[ClassOut]
    imports: list[ImportOut]
    parse_errors: bool


@router.get("/{project_id}/parse", response_model=list[FileParseOut])
def parse_project(project_id: str):
    try:
        project_path = get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    files = walk_project(str(project_path))
    python_files = [f for f in files if f.language == "python"]

    results = []
    for f in python_files:
        parsed = parse_python_file(f.absolute_path)
        results.append(
            FileParseOut(
                path=f.path,
                functions=[FunctionOut(**vars(fn)) for fn in parsed.functions],
                classes=[ClassOut(**vars(c)) for c in parsed.classes],
                imports=[ImportOut(**vars(i)) for i in parsed.imports],
                parse_errors=parsed.parse_errors,
            )
        )

    return results