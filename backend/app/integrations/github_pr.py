"""
Open a GitHub pull request containing an APPROVED patch.

Safety design:
  - Nothing is written to GitHub unless the caller says dry_run=False AND confirm=True.
  - dry_run (the default) still reads the real file from GitHub and checks the
    patch applies cleanly, so you find out about problems before publishing.
  - The token comes from the server's environment (GITHUB_TOKEN), never from a request.
  - The diff must touch exactly the one file named in the request.
  - The patch is applied strictly to the file as it is on GitHub right now. If the
    file changed since the diff was made, we refuse instead of guessing.
  - Changes always go on a NEW branch and a PR; we never push to your main branch.
  - If you can't push to the repo (or use_fork=True), we use a fork.
"""

import ast
import base64
import re
import time
import uuid

import httpx

from app.analysis.diff_utils import PatchError, apply_unified_diff, parse_diff

API = "https://api.github.com"


class GitHubError(Exception):
    pass


def parse_repo_url(url: str) -> tuple[str, str]:
    m = re.match(r"^(?:https?://github\.com/|git@github\.com:)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise GitHubError("repo_url must look like https://github.com/OWNER/REPO")
    return m.group(1), m.group(2)


class GitHubClient:
    def __init__(self, token: str, transport=None, sleep=time.sleep):
        self._c = httpx.Client(
            base_url=API, transport=transport, timeout=30.0,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-codebase-engineer"},
        )
        self._sleep = sleep

    def request(self, method: str, path: str, ok=(200, 201, 202), **kw) -> dict:
        try:
            r = self._c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise GitHubError(f"Could not reach GitHub: {e}")
        if r.status_code in ok:
            return r.json() if r.content else {}
        msg = ""
        try:
            msg = r.json().get("message", "")
        except Exception:
            pass
        hints = {
            401: "GitHub rejected the token (invalid or expired).",
            403: "The token isn't allowed to do that (needs Contents and Pull requests write access, "
                 "or you hit a rate limit).",
            404: "Not found, or the token has no access to that repository.",
            422: "GitHub refused the request (a branch or pull request may already exist).",
        }
        raise GitHubError(f"GitHub {r.status_code} on {method} {path}: {hints.get(r.status_code, '')} {msg}".strip())

    def close(self):
        self._c.close()


def _check_diff_targets(diff: str, file_path: str) -> None:
    parsed = parse_diff(diff)
    if len(parsed["files"]) != 1:
        raise GitHubError("The diff must change exactly one file.")
    new_path = parsed["files"][0]["new_path"]
    new_path = new_path[2:] if new_path.startswith("b/") else new_path
    if new_path != file_path:
        raise GitHubError(f"The diff changes `{new_path}` but file_path is `{file_path}`.")


def build_pr_body(rule_id: str | None, explanation: str | None, verdict: str | None,
                  warnings: list[str] | None, extra: str | None) -> str:
    lines = ["## What this changes", explanation or "A fix proposed by AI Codebase Engineer."]
    if rule_id:
        lines += ["", f"Finding: `{rule_id}`"]
    if verdict:
        lines += ["", f"Verification: **{verdict}**" + (" (tests passed before and after the patch)"
                                                       if verdict == "safe_by_tests" else "")]
    if warnings:
        lines += ["", "## Please check", *[f"- {w}" for w in warnings]]
    if extra:
        lines += ["", extra]
    lines += ["", "---", "Opened by AI Codebase Engineer after a human reviewed and approved the diff. "
                       "Review before merging."]
    return "\n".join(lines)


def open_pull_request(token: str, repo_url: str, file_path: str, diff: str, title: str, body: str,
                      base_branch: str | None = None, use_fork: bool = False, dry_run: bool = True,
                      branch_hint: str = "fix", transport=None, sleep=time.sleep) -> dict:
    owner, repo = parse_repo_url(repo_url)
    file_path = file_path.replace("\\", "/").lstrip("/")
    _check_diff_targets(diff, file_path)

    gh = GitHubClient(token, transport=transport, sleep=sleep)
    try:
        info = gh.request("GET", f"/repos/{owner}/{repo}")
        base = base_branch or info["default_branch"]
        can_push = bool(info.get("permissions", {}).get("push"))
        fork_needed = use_fork or not can_push

        # read the file as it is on GitHub now and make sure the patch applies
        f = gh.request("GET", f"/repos/{owner}/{repo}/contents/{file_path}", params={"ref": base})
        if f.get("type") != "file" or "content" not in f:
            raise GitHubError(f"`{file_path}` is not a regular file on branch `{base}`.")
        old_text = base64.b64decode(f["content"]).decode("utf-8")
        try:
            new_text = apply_unified_diff(old_text, diff)
        except PatchError as e:
            raise GitHubError(f"The patch doesn't apply to `{file_path}` on `{base}`: {e} "
                              "Re-run the fix against the latest code.")
        if file_path.endswith(".py"):
            try:
                ast.parse(new_text)
            except SyntaxError as e:
                raise GitHubError(f"The patched file would not be valid Python (line {e.lineno}).")

        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", branch_hint).strip("-")[:30] or "fix"
        branch = f"aice/{slug}-{uuid.uuid4().hex[:6]}"
        plan = {
            "repo": f"{owner}/{repo}", "base_branch": base, "new_branch": branch, "file": file_path,
            "uses_fork": fork_needed, "title": title, "body": body,
            "diff_stats": parse_diff(diff)["stats"], "patch_applies_cleanly": True,
        }
        if dry_run:
            plan["dry_run"] = True
            plan["note"] = "Nothing was written to GitHub. Set dry_run=false and confirm=true to open the PR."
            return plan

        work_owner, work_repo = owner, repo
        if fork_needed:
            me = gh.request("GET", "/user")["login"]
            if me.lower() != owner.lower():
                gh.request("POST", f"/repos/{owner}/{repo}/forks", ok=(200, 201, 202), json={})
                for _ in range(15):                       # forks are created asynchronously
                    try:
                        gh.request("GET", f"/repos/{me}/{repo}")
                        break
                    except GitHubError:
                        sleep(2)
                else:
                    raise GitHubError("The fork wasn't ready after 30 seconds. Try again in a minute.")
                work_owner = me

        base_sha = gh.request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{base}")["object"]["sha"]
        gh.request("POST", f"/repos/{work_owner}/{repo}/git/refs",
                   json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        gh.request("PUT", f"/repos/{work_owner}/{repo}/contents/{file_path}", json={
            "message": title, "branch": branch, "sha": f["sha"],
            "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
        })
        pr = gh.request("POST", f"/repos/{owner}/{repo}/pulls", json={
            "title": title, "body": body, "base": base,
            "head": branch if work_owner == owner else f"{work_owner}:{branch}",
            "maintainer_can_modify": True,
        })
        plan.update({"dry_run": False, "pr_url": pr["html_url"], "pr_number": pr["number"],
                     "note": "Pull request opened. Review it on GitHub before merging."})
        return plan
    finally:
        gh.close()
