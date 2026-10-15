from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.ingestion.ingest_service import get_project_path, IngestError
from app.integrations.github_pr import GitHubError, build_pr_body, open_pull_request

router = APIRouter()


class OpenPRRequest(BaseModel):
    repo_url: str                      # https://github.com/OWNER/REPO
    file_path: str                     # path inside the repo, e.g. "auth/hash.py"
    diff: str                          # the unified diff you reviewed and approved
    title: str | None = None
    base_branch: str | None = None     # default: the repo's default branch
    use_fork: bool = False             # fork first (automatic if you can't push to the repo)
    dry_run: bool = True               # True = only check; nothing is written to GitHub
    confirm: bool = False              # must be True to really open the PR
    rule_id: str | None = None         # optional context for the PR description
    explanation: str | None = None
    verdict: str | None = None         # e.g. "safe_by_tests"
    warnings: list[str] = []


@router.post("/{project_id}/open-pr")
def open_pr(project_id: str, payload: OpenPRRequest):
    """Open a pull request with an approved patch. Defaults to a dry run that only checks the
    patch against the live file on GitHub. Real PRs need dry_run=false AND confirm=true."""
    try:
        get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    token = settings.github_token
    if not token:
        raise HTTPException(400, "No GitHub token configured. Add GITHUB_TOKEN=... to backend/.env and "
                                 "restart the server. Create a fine-grained token at "
                                 "github.com/settings/personal-access-tokens with Contents: Read and write "
                                 "and Pull requests: Read and write for the repository.")
    if not payload.dry_run and not payload.confirm:
        raise HTTPException(400, "Opening a pull request publishes to GitHub. Set confirm=true to proceed "
                                 "(or keep dry_run=true to only check).")

    title = payload.title or f"Fix {payload.rule_id or 'issue'} in {payload.file_path}"
    body = build_pr_body(payload.rule_id, payload.explanation, payload.verdict, payload.warnings, None)
    try:
        return open_pull_request(
            token, payload.repo_url, payload.file_path, payload.diff, title, body,
            base_branch=payload.base_branch, use_fork=payload.use_fork, dry_run=payload.dry_run,
            branch_hint=f"{payload.rule_id or 'fix'}",
        )
    except GitHubError as e:
        raise HTTPException(400, str(e))
