"""
Scan history: every scan the web app runs is saved, so you can see what changed between scans
(what you fixed, what is new). Stored as one small JSON file per project under data/history/.

Findings are matched between scans by (file, rule, the offending line's text), not by line number,
because line numbers shift as soon as someone edits code above a finding.
"""

import json
import os
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

MAX_SCANS = 30
_lock = threading.Lock()
_KEEP = ("file_path", "line", "rule_id", "severity", "title", "code")


def _file(project_id: str) -> Path:
    safe = "".join(c for c in project_id if c.isalnum())
    return Path(settings.data_dir).parent / "history" / f"{safe}.json"


def _load(project_id: str) -> list[dict]:
    try:
        return json.loads(_file(project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(project_id: str, scans: list[dict]) -> None:
    path = _file(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(scans, fh)
    os.replace(tmp, path)


def _key(f: dict) -> tuple:
    return (f["file_path"], f["rule_id"], f.get("code", "").strip())


def record_scan(project_id: str, result: dict) -> dict:
    """Save one scan result (the dict a scan job returns). Returns its summary."""
    with _lock:
        scans = _load(project_id)
        entry = {
            "id": (scans[-1]["id"] + 1) if scans else 1,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files_scanned": result.get("files_scanned", 0),
            "total": result.get("total_findings", len(result.get("findings", []))),
            "by_severity": result.get("by_severity", {}),
            "findings": [{k: f.get(k) for k in _KEEP} for f in result.get("findings", [])],
        }
        if scans:
            d = diff(scans[-1], entry)
            if not d["new"] and not d["fixed"]:
                # nothing changed since the last saved scan: refresh its time instead of piling up copies
                scans[-1]["at"] = entry["at"]
                scans[-1]["files_scanned"] = entry["files_scanned"]
                _save(project_id, scans[-MAX_SCANS:])
                return {**summary(scans[-1], None), "new": 0, "fixed": 0, "first": False, "same": True}
        scans.append(entry)
        _save(project_id, scans[-MAX_SCANS:])
    return summary(entry, scans[-2] if len(scans) > 1 else None)


def diff(prev: dict | None, cur: dict) -> dict:
    """What is new, fixed and unchanged going from `prev` to `cur`."""
    if prev is None:
        return {"new": [], "fixed": [], "unchanged": len(cur["findings"]), "first": True}
    before, after = Counter(_key(f) for f in prev["findings"]), Counter(_key(f) for f in cur["findings"])
    new_budget, fixed_budget = after - before, before - after
    new, fixed = [], []
    for f in cur["findings"]:
        k = _key(f)
        if new_budget.get(k, 0) > 0:
            new_budget[k] -= 1
            new.append(f)
    for f in prev["findings"]:
        k = _key(f)
        if fixed_budget.get(k, 0) > 0:
            fixed_budget[k] -= 1
            fixed.append(f)
    return {"new": new, "fixed": fixed, "unchanged": len(cur["findings"]) - len(new), "first": False}


def summary(entry: dict, prev: dict | None) -> dict:
    d = diff(prev, entry)
    return {
        "id": entry["id"], "at": entry["at"], "files_scanned": entry["files_scanned"],
        "total": entry["total"], "by_severity": entry["by_severity"],
        "new": len(d["new"]), "fixed": len(d["fixed"]), "first": d["first"],
    }


def list_scans(project_id: str) -> list[dict]:
    scans = _load(project_id)
    return [summary(s, scans[i - 1] if i else None) for i, s in enumerate(scans)][::-1]   # newest first


def get_scan(project_id: str, scan_id: int) -> dict | None:
    scans = _load(project_id)
    for i, s in enumerate(scans):
        if s["id"] == scan_id:
            d = diff(scans[i - 1] if i else None, s)
            return {**summary(s, scans[i - 1] if i else None), "new_findings": d["new"],
                    "fixed_findings": d["fixed"], "unchanged": d["unchanged"]}
    return None


def clear(project_id: str) -> None:
    with _lock:
        try:
            _file(project_id).unlink()
        except OSError:
            pass
