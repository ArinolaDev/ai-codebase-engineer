from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.ingestion.ingest_service import get_project_path, IngestError
from app.analysis.report import build_report_data, render_html, render_markdown

router = APIRouter()


@router.get("/{project_id}/report")
def project_report(project_id: str, format: str = "html", min_severity: str = "low"):
    """A shareable report of the scan. format=html opens a printable page (Ctrl+P, Save as PDF);
    format=md downloads Markdown. Read-only."""
    if format not in ("html", "md"):
        raise HTTPException(400, "format must be html or md")
    if min_severity not in ("high", "medium", "low"):
        raise HTTPException(400, "min_severity must be high, medium, or low")
    try:
        root = str(get_project_path(project_id))
    except IngestError as e:
        raise HTTPException(404, str(e))

    data = build_report_data(root, project_id, min_severity)
    if format == "md":
        return Response(render_markdown(data), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="report-{project_id}.md"'})
    return Response(render_html(data), media_type="text/html; charset=utf-8")
