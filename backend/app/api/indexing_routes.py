from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.ingestion.file_walker import walk_project
from app.parsing.python_parser import parse_python_file
from app.indexing.chunker import chunk_parsed_file
from app.indexing.vector_store import index_chunks, search, reset_project_index

router = APIRouter()


class IndexResponse(BaseModel):
    project_id: str
    files_processed: int
    chunks_indexed: int


class SearchHit(BaseModel):
    chunk_id: str
    distance: float
    file_path: str
    symbol_name: str
    symbol_type: str
    start_line: int
    end_line: int
    snippet: str


@router.post("/{project_id}/index", response_model=IndexResponse)
def build_index(project_id: str):
    try:
        project_path = get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    reset_project_index(project_id)  # clean slate on re-index

    files = walk_project(str(project_path))
    python_files = [f for f in files if f.language == "python"]

    total_chunks = 0
    for f in python_files:
        parsed = parse_python_file(f.absolute_path)
        chunks = chunk_parsed_file(parsed, f.absolute_path)
        total_chunks += index_chunks(project_id, chunks)

    return IndexResponse(
        project_id=project_id,
        files_processed=len(python_files),
        chunks_indexed=total_chunks,
    )


@router.get("/{project_id}/search", response_model=list[SearchHit])
def search_project(project_id: str, q: str, top_k: int = 5):
    try:
        get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    results = search(project_id, q, top_k)
    return [
        SearchHit(
            chunk_id=r["chunk_id"],
            distance=r["distance"],
            file_path=r["metadata"]["file_path"],
            symbol_name=r["metadata"]["symbol_name"],
            symbol_type=r["metadata"]["symbol_type"],
            start_line=r["metadata"]["start_line"],
            end_line=r["metadata"]["end_line"],
            snippet=r["snippet"],
        )
        for r in results
    ]