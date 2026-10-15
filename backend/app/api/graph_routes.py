from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.dep_graph import cached_graph

router = APIRouter()


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    stats: dict
    notes: list[str]


class FunctionGraphResponse(BaseModel):
    node: dict
    callers: list[dict]
    callees: list[dict]
    impact: list[dict]
    summary: str


@router.get("/{project_id}/graph", response_model=GraphResponse)
def project_graph(project_id: str):
    """Full dependency graph: files, classes, functions, imports and calls."""
    try:
        root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    return GraphResponse(**cached_graph(root).to_dict())


@router.get("/{project_id}/graph/function", response_model=FunctionGraphResponse)
def function_graph(project_id: str,
                   file: str = Query(..., description="e.g. pkg/pricing.py"),
                   name: str = Query(..., description="function or Class.method"),
                   depth: int = Query(3, ge=1, le=6)):
    """Who calls this function, what it calls, and everything affected if you change it."""
    try:
        root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))
    g = cached_graph(root)
    node = g.find(file.replace("\\", "/"), name)
    if node is None:
        raise HTTPException(404, f"No function `{name}` in {file}. Check the file path and name.")
    from dataclasses import asdict
    callers = [{"node": e.source, "line": e.line, "approx": e.approx} for e in g.callers(node.id)]
    callees = [{"node": e.target, "line": e.line, "approx": e.approx} for e in g.callees(node.id)]
    impact = g.impact(node.id, depth)
    return FunctionGraphResponse(
        node=asdict(node), callers=callers, callees=callees, impact=impact,
        summary=f"{len(callers)} direct caller(s); {len(impact)} function(s) affected within {depth} hop(s).",
    )
