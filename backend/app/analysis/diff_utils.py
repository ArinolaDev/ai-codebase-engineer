"""
Unified-diff helpers (no LLM, no git binary needed).

  parse_diff(text)          -> structured hunks/lines for a UI (unified + side-by-side)
  apply_unified_diff(a, d)  -> strictly apply a single-file diff to text, or raise PatchError
  patch_commands(path)      -> copy-paste commands for applying a saved patch

apply_unified_diff is strict on purpose: every context and removed line must
match the target exactly. If the file has moved on since the diff was made,
we refuse instead of guessing.
"""

import re
from dataclasses import dataclass, asdict

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(Exception):
    pass


@dataclass
class DiffLine:
    type: str            # "context" | "add" | "del"
    old_no: int | None
    new_no: int | None
    text: str


def parse_diff(diff_text: str) -> dict:
    """Return {files:[{old_path,new_path,hunks:[{header,lines:[...],side_by_side:[...]}]}], stats}."""
    files: list[dict] = []
    cur_file = None
    cur_hunk = None
    old_no = new_no = 0
    added = removed = 0

    for raw in diff_text.splitlines():
        if raw.startswith("--- "):
            cur_file = {"old_path": raw[4:].strip(), "new_path": "", "hunks": []}
            files.append(cur_file)
            cur_hunk = None
            continue
        if raw.startswith("+++ ") and cur_file is not None:
            cur_file["new_path"] = raw[4:].strip()
            continue
        m = _HUNK.match(raw)
        if m and cur_file is not None:
            old_no, new_no = int(m.group(1)), int(m.group(3))
            cur_hunk = {"header": raw, "lines": []}
            cur_file["hunks"].append(cur_hunk)
            continue
        if cur_hunk is None or raw.startswith("\\"):
            continue
        tag, text = (raw[:1], raw[1:]) if raw else (" ", "")
        if tag == "+":
            cur_hunk["lines"].append(asdict(DiffLine("add", None, new_no, text)))
            new_no += 1
            added += 1
        elif tag == "-":
            cur_hunk["lines"].append(asdict(DiffLine("del", old_no, None, text)))
            old_no += 1
            removed += 1
        else:
            cur_hunk["lines"].append(asdict(DiffLine("context", old_no, new_no, text)))
            old_no += 1
            new_no += 1

    for f in files:
        for h in f["hunks"]:
            h["side_by_side"] = _side_by_side(h["lines"])

    return {"files": files, "stats": {"added": added, "removed": removed, "files": len(files)}}


def _side_by_side(lines: list[dict]) -> list[dict]:
    """Pair deletions with the additions that follow them so a UI can show two columns."""
    rows, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln["type"] == "context":
            rows.append({"left": ln, "right": ln})
            i += 1
            continue
        dels, adds = [], []
        while i < len(lines) and lines[i]["type"] == "del":
            dels.append(lines[i]); i += 1
        while i < len(lines) and lines[i]["type"] == "add":
            adds.append(lines[i]); i += 1
        for k in range(max(len(dels), len(adds))):
            rows.append({"left": dels[k] if k < len(dels) else None,
                         "right": adds[k] if k < len(adds) else None})
    return rows


def apply_unified_diff(old_text: str, diff_text: str) -> str:
    """Apply a single-file unified diff. Raises PatchError unless it applies exactly."""
    trailing_nl = old_text.endswith("\n")
    lines = old_text.splitlines()
    parsed = parse_diff(diff_text)
    if len(parsed["files"]) != 1:
        raise PatchError("Expected a diff for exactly one file.")
    hunks = parsed["files"][0]["hunks"]
    if not hunks:
        raise PatchError("The diff has no changes.")

    out: list[str] = []
    pos = 0  # index into `lines` already copied
    for h in hunks:
        old_block = [l["text"] for l in h["lines"] if l["type"] in ("context", "del")]
        new_block = [l["text"] for l in h["lines"] if l["type"] in ("context", "add")]
        m = _HUNK.match(h["header"])
        start = max(int(m.group(1)) - 1, 0)
        if int(m.group(2) or 1) == 0:
            start = int(m.group(1))        # pure insertion: "-N,0" means after line N
        idx = _locate(lines, old_block, start, pos)
        if idx is None:
            raise PatchError(
                "The file no longer matches the diff (its lines changed since the diff was made)."
            )
        out.extend(lines[pos:idx])
        out.extend(new_block)
        pos = idx + len(old_block)
    out.extend(lines[pos:])
    return "\n".join(out) + ("\n" if trailing_nl else "")


def _locate(lines: list[str], block: list[str], expected: int, floor: int) -> int | None:
    n = len(block)
    if expected >= floor and lines[expected:expected + n] == block:
        return expected
    # tolerate the file shifting by a few lines, but only if the match is unambiguous
    hits = [i for i in range(floor, len(lines) - n + 1) if lines[i:i + n] == block]
    if n and len(hits) == 1:
        return hits[0]
    return None


def patch_commands(path_in_repo: str, patch_filename: str = "fix.patch") -> dict:
    return {
        "save_as": patch_filename,
        "check": f"git apply --check {patch_filename}",
        "apply": f"git apply {patch_filename}",
        "note": f"Run from the repository root. Changes {path_in_repo}. Review with `git diff` before committing.",
    }
