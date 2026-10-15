"""Turn fix / verify / retry results into JSON for the API (shared by routes and jobs)."""

from app.analysis.diff_utils import parse_diff, patch_commands


def impact_for(project_root: str, rel_path: str, line: int) -> dict | None:
    """Who calls the function being changed. Best effort: never fails the request."""
    try:
        from app.analysis.dep_graph import cached_graph
        g = cached_graph(project_root)
        node = g.node_at_line(rel_path.replace("\\", "/"), line)
        if node is None:
            return None
        direct = [{"node": e.source, "line": e.line, "approx": e.approx} for e in g.callers(node.id)]
        affected = g.impact(node.id, 3)
        return {
            "function": node.id, "direct_callers": direct, "affected": affected,
            "summary": (f"{node.name} has {len(direct)} direct caller(s); changing it can affect "
                        f"{len(affected)} function(s) within 3 hops."),
        }
    except Exception:
        return None


def proposal_dict(p, rel_path: str) -> dict:
    return {
        "rule_id": p.rule_id, "line": p.line, "method": p.method, "verified": p.verified,
        "explanation": p.explanation, "diff": p.diff, "diff_structured": parse_diff(p.diff),
        "checks": p.checks, "warnings": p.warnings, "patch": patch_commands(rel_path),
    }


def verify_dict(r, rel_path: str, project_root: str | None = None) -> dict:
    p = r.proposal
    d = {
        "verdict": r.verdict, "summary": r.summary,
        "diff": p.diff, "diff_structured": parse_diff(p.diff),
        "fix_method": p.method, "fix_checks": p.checks, "fix_warnings": p.warnings,
        "tests_before_passed": r.tests_before_passed, "tests_after_passed": r.tests_after_passed,
        "regressions": r.regressions, "notes": r.notes, "test_code_used": r.test_code_used,
        "patch": patch_commands(rel_path),
        "safety_note": "Tests ran in a temporary copy, not a sandbox. Only use on projects you trust.",
        "impact": impact_for(project_root, rel_path, p.line) if project_root else None,
    }
    return d


def loop_dict(lr, rel_path: str, project_root: str | None = None) -> dict:
    return {
        "outcome": lr.outcome, "message": lr.message,
        "attempts": [{
            "number": a.number, "verdict": a.verdict, "summary": a.summary, "method": a.method,
            "diff": a.diff, "diff_structured": parse_diff(a.diff), "fix_checks": a.fix_checks,
            "regressions": a.regressions, "feedback_given": a.feedback_given,
        } for a in lr.attempts],
        "best": verify_dict(lr.best, rel_path, project_root) if lr.best else None,
    }
