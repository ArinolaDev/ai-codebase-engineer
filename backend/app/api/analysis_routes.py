from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.ingestion.file_walker import walk_project
from app.analysis.pattern_scanner import scan_python_file, sort_findings

router = APIRouter()


class FindingOut(BaseModel):
    file_path: str
    line: int
    rule_id: str
    severity: str
    title: str
    explanation: str
    suggestion: str
    code: str


class ScanResponse(BaseModel):
    project_id: str
    files_scanned: int
    total_findings: int
    by_severity: dict[str, int]
    findings: list[FindingOut]


@router.get("/{project_id}/scan", response_model=ScanResponse)
def scan_project(project_id: str, min_severity: str = "low"):
    """Run the deterministic pattern scanner over every Python file.
    min_severity: 'high' | 'medium' | 'low' (default shows everything)."""
    if min_severity not in ("high", "medium", "low"):
        raise HTTPException(400, "min_severity must be high, medium, or low")

    try:
        project_path = get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    files = [f for f in walk_project(str(project_path)) if f.language == "python"]

    findings = []
    for f in files:
        findings.extend(scan_python_file(f.absolute_path, f.path))
    findings = sort_findings(findings)

    order = {"high": 0, "medium": 1, "low": 2}
    findings = [f for f in findings if order[f.severity] <= order[min_severity]]

    by_severity = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        by_severity[f.severity] += 1

    return ScanResponse(
        project_id=project_id,
        files_scanned=len(files),
        total_findings=len(findings),
        by_severity=by_severity,
        findings=[FindingOut(**vars(f)) for f in findings],
    )