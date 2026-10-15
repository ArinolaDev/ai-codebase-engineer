"""
Fix -> verify -> retry loop (Phase 8).

A deterministic rule fix is either right or wrong, so it gets ONE attempt.
A model-written fix gets up to `max_attempts` (hard cap 3). After each failed
attempt we hand the model the concrete reason (failed scanner check, or the
regression's assertion message) and ask again. Every attempt is kept, so a
human can see what was tried.

The loop only ever STOPS EARLY on success. It never applies anything: the
result is still a proposal that a person approves.
"""

from dataclasses import dataclass, field

from app.verification.patch_verifier import VerifyResult, verify_fix

HARD_CAP = 3


@dataclass
class Attempt:
    number: int
    verdict: str
    summary: str
    diff: str
    method: str
    fix_checks: list[str]
    regressions: dict[str, str]
    feedback_given: str | None


@dataclass
class LoopResult:
    outcome: str                 # success | untested | gave_up
    attempts: list[Attempt] = field(default_factory=list)
    best: VerifyResult | None = None
    message: str = ""


def _feedback(r: VerifyResult) -> str:
    if r.verdict == "rejected":
        return "The patch failed automatic checks: " + " ".join(r.proposal.checks)
    if r.verdict == "unsafe":
        lines = [f"- test `{name}` now fails: {why}" for name, why in list(r.regressions.items())[:3]]
        return ("The patch removed the problem but broke existing behavior. These tests passed before "
                "your patch and fail after it:\n" + "\n".join(lines) +
                "\nKeep the return value and behavior identical for normal inputs.")
    return r.summary


def fix_with_retries(project_root: str, rel_path: str, line: int, rule_id: str,
                     test_code: str | None = None, auto_tests: bool = False,
                     max_attempts: int = 3, progress=None,
                     fix_llm=None, test_llm=None) -> LoopResult:
    p = progress or (lambda *a, **k: None)
    max_attempts = max(1, min(max_attempts, HARD_CAP))
    result = LoopResult(outcome="gave_up")
    feedback = None

    for n in range(1, max_attempts + 1):
        p("attempt", f"Attempt {n} of {max_attempts}", n=n, of=max_attempts)
        r = verify_fix(project_root, rel_path, line, rule_id, test_code=test_code,
                       auto_tests=auto_tests, fix_llm=fix_llm, test_llm=test_llm,
                       fix_feedback=feedback, progress=progress)
        result.attempts.append(Attempt(
            number=n, verdict=r.verdict, summary=r.summary, diff=r.proposal.diff,
            method=r.proposal.method, fix_checks=r.proposal.checks,
            regressions=r.regressions, feedback_given=feedback,
        ))

        if r.verdict == "safe_by_tests":
            result.outcome, result.best = "success", r
            result.message = f"Verified on attempt {n}."
            return result
        if r.verdict == "untested":
            # the patch is valid but there is nothing to test it against: retrying can't improve that
            result.outcome, result.best = "untested", r
            result.message = r.summary
            return result
        if r.proposal.method == "rule":
            result.message = ("The rule-based fix breaks behavior and a rule can't be retried. "
                              "Review the regression and decide manually.")
            return result
        feedback = _feedback(r)

    result.message = f"No safe patch after {max_attempts} attempts. All attempts are listed for review."
    return result
