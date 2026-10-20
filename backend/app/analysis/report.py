"""
Builds a shareable report from a project's scan: Markdown (for repos, tickets, chat) and a
self-contained, print-friendly HTML page (open it, press Ctrl+P, choose "Save as PDF").

Read-only: it scans the project and describes what it found. Nothing is changed.
Python only for now, like the scanner itself.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from datetime import datetime

from app.analysis.pattern_scanner import scan_python_file, sort_findings
from app.ingestion.file_walker import summarize_languages, walk_project

SEVERITIES = ["high", "medium", "low"]
SEV_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}
SEV_MEANING = {
    "high": "Fix first: security holes or code that can run attacker-controlled input.",
    "medium": "Worth fixing: weak practices or bugs that will bite later.",
    "low": "Tidy up: small risks and bad habits.",
}


# --------------------------------------------------------------------------- data

def build_report_data(project_root: str, project_id: str, min_severity: str = "low") -> dict:
    files = walk_project(project_root)
    py_files = [f for f in files if f.language == "python"]

    findings = []
    for f in py_files:
        findings.extend(scan_python_file(f.absolute_path, f.path))
    findings = sort_findings(findings)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings = [f for f in findings if order[f.severity] <= order[min_severity]]

    by_sev = Counter(f.severity for f in findings)
    per_file: dict[str, Counter] = defaultdict(Counter)
    for f in findings:
        per_file[f.file_path][f.severity] += 1

    structure = None
    try:   # best effort: the report is still useful without the call graph
        from app.analysis.dep_graph import cached_graph
        s = cached_graph(project_root).stats()
        structure = {
            "functions": s["functions"], "methods": s["methods"], "classes": s["classes"],
            "call_edges": s["call_edges"], "import_edges": s["import_edges"],
            "import_cycles": [" -> ".join(c) for c in s["import_cycles"]],
            "possibly_unused": [_pretty_node(n) for n in s["possibly_unused"]],
        }
    except Exception:  # noqa: BLE001
        structure = None

    return {
        "project_id": project_id,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "min_severity": min_severity,
        "files_total": len(files),
        "files_scanned": len(py_files),
        "languages": summarize_languages(files),
        "total": len(findings),
        "by_severity": {s: by_sev.get(s, 0) for s in SEVERITIES},
        "findings": [vars(f) for f in findings],
        "per_file": [{"file": k, **{s: v.get(s, 0) for s in SEVERITIES}, "total": sum(v.values())}
                     for k, v in sorted(per_file.items(), key=lambda kv: (-kv[1].get("high", 0), -sum(kv[1].values()), kv[0]))],
        "structure": structure,
    }


def _pretty_node(node_id: str) -> str:
    file, _, name = node_id.partition("::")
    return f"{name or '<module>'} ({file})"


def _headline(d: dict) -> str:
    if not d["files_scanned"]:
        return "No Python files were found, so nothing was scanned."
    if not d["total"]:
        return "The scanner found no issues in this project."
    h, m, l = (d["by_severity"][s] for s in SEVERITIES)
    parts = []
    if h:
        parts.append(f"{h} high-severity issue{'s' if h != 1 else ''} to fix first")
    if m:
        parts.append(f"{m} medium")
    if l:
        parts.append(f"{l} low")
    return f"{d['total']} issue{'s' if d['total'] != 1 else ''} found: " + ", ".join(parts) + "."


LIMITS = [
    "The scanner is rule-based: it matches known risky patterns (hardcoded secrets, SQL built from strings, "
    "shell and eval calls, weak hashes, bare excepts, mutable defaults). It is exact about what it matches "
    "and blind to everything else, so a clean report is not a guarantee of safe code.",
    "Logic bugs (wrong results, off-by-one errors) are not found by this scan. The model review in the app "
    "looks for those, but its findings are leads to check, not facts.",
    "Only Python files are analysed. Other languages are listed but not scanned.",
    "The call graph is static and approximate: calls made through variables, callbacks or reflection can be "
    "missed, so \"possibly unused\" means \"nothing in the project visibly calls it\".",
]


# --------------------------------------------------------------------------- markdown

def _fence(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(d: dict) -> str:
    out: list[str] = []
    out += [f"# Code review report", "",
            f"Project `{d['project_id']}` · generated {d['generated']} · rule-based scanner (Python)", "",
            f"> {_headline(d)}", ""]

    out += ["## Summary", "", "| | |", "|---|---|",
            f"| Files in project | {d['files_total']} |",
            f"| Python files scanned | {d['files_scanned']} |",
            f"| High | {d['by_severity']['high']} |",
            f"| Medium | {d['by_severity']['medium']} |",
            f"| Low | {d['by_severity']['low']} |", ""]
    if d["languages"]:
        out += ["Languages: " + ", ".join(f"{k} ({v})" for k, v in d["languages"].items()), ""]

    if d["findings"]:
        out += ["## Findings", ""]
        n = 0
        for sev in SEVERITIES:
            group = [f for f in d["findings"] if f["severity"] == sev]
            if not group:
                continue
            out += [f"### {SEV_LABEL[sev]} ({len(group)})", "", f"_{SEV_MEANING[sev]}_", ""]
            for f in group:
                n += 1
                code = f["code"].strip("\n")
                fence = _fence(code)
                out += [f"#### {n}. {f['title']} (`{f['rule_id']}`)", "",
                        f"`{f['file_path']}:{f['line']}`", "",
                        f"{fence}python", code, fence, "",
                        f"**Why it matters:** {f['explanation']}", "",
                        f"**Suggested fix:** {f['suggestion']}", ""]

    if d["per_file"]:
        out += ["## By file", "", "| File | High | Medium | Low | Total |", "|---|---:|---:|---:|---:|"]
        for r in d["per_file"]:
            out.append(f"| `{_md_cell(r['file'])}` | {r['high']} | {r['medium']} | {r['low']} | {r['total']} |")
        out.append("")

    st = d["structure"]
    if st:
        out += ["## Project structure", "",
                f"- {st['functions']} functions, {st['methods']} methods, {st['classes']} classes",
                f"- {st['call_edges']} call links and {st['import_edges']} import links between files",
                f"- Import cycles: {len(st['import_cycles']) or 'none'}"]
        for c in st["import_cycles"]:
            out.append(f"  - {c}")
        out.append("")
        if st["possibly_unused"]:
            out += [f"### Possibly unused ({len(st['possibly_unused'])})", "",
                    "Nothing in the project visibly calls these. Check before deleting.", ""]
            out += [f"- `{u}`" for u in st["possibly_unused"][:40]]
            if len(st["possibly_unused"]) > 40:
                out.append(f"- ... and {len(st['possibly_unused']) - 40} more")
            out.append("")

    out += ["## How to read this report", ""] + [f"- {t}" for t in LIMITS] + [""]
    return "\n".join(out)


# --------------------------------------------------------------------------- html

_CSS = """
:root{--ink:#14181F;--muted:#5B6577;--line:#E3E7EE;--soft:#F5F7FA;--accent:#C4F542;
--high:#B3261E;--high-bg:#FDECEA;--med:#8A5300;--med-bg:#FFF3DC;--low:#0B5CAD;--low-bg:#E6F1FC}
*{box-sizing:border-box}
body{margin:0;background:#EEF0F4;color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.bar{background:#fff;border-bottom:1px solid var(--line);padding:12px 24px;display:flex;gap:14px;align-items:center;position:sticky;top:0}
.bar b{font-weight:650}.bar span{color:var(--muted);font-size:13px}
.bar button{margin-left:auto;font:inherit;font-weight:600;padding:8px 16px;border-radius:8px;border:0;background:var(--ink);color:#fff;cursor:pointer}
.page{max-width:880px;margin:24px auto;background:#fff;padding:48px 56px 40px;border:1px solid var(--line);border-top:6px solid var(--accent);border-radius:4px}
h1{font-size:30px;line-height:1.15;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:19px;margin:36px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:22px 0 4px;text-transform:uppercase;letter-spacing:.06em}
.meta{color:var(--muted);font-size:13.5px}
.headline{margin:18px 0 0;font-size:17px;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 6px}
.tile{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--soft)}
.tile .n{font-size:26px;font-weight:700;line-height:1.1}.tile .l{font-size:12.5px;color:var(--muted)}
.tile.high .n{color:var(--high)}.tile.medium .n{color:var(--med)}.tile.low .n{color:var(--low)}
.stack{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:14px 0 4px;background:var(--line)}
.stack i{display:block;height:100%}.stack .high{background:var(--high)}.stack .medium{background:#E09A1B}.stack .low{background:#3F8FDB}
.sevnote{color:var(--muted);font-size:13.5px;margin:0 0 10px}
.card{border:1px solid var(--line);border-left-width:5px;border-radius:8px;padding:14px 16px;margin:12px 0;break-inside:avoid;page-break-inside:avoid}
.card.high{border-left-color:var(--high)}.card.medium{border-left-color:#E09A1B}.card.low{border-left-color:#3F8FDB}
.card .t{font-weight:650;font-size:15.5px}
.chip{display:inline-block;font-size:11.5px;font-weight:650;padding:1px 8px;border-radius:99px;margin-left:8px;vertical-align:1px;border:1px solid var(--line);color:var(--muted);background:var(--soft)}
.chip.high{color:var(--high);background:var(--high-bg);border-color:#F2C3BF}.chip.medium{color:var(--med);background:var(--med-bg);border-color:#EFD39B}.chip.low{color:var(--low);background:var(--low-bg);border-color:#B9D6F2}
.loc{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);margin:2px 0 8px}
pre{margin:0 0 10px;padding:10px 12px;background:#F2F4F8;border:1px solid var(--line);border-radius:6px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.row{margin:4px 0;font-size:14.5px}.row b{font-weight:650}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--soft);padding:1px 5px;border-radius:4px}
ul{margin:8px 0;padding-left:22px}li{margin:4px 0}
.foot{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}
@media (max-width:700px){.page{padding:28px 20px}.tiles{grid-template-columns:repeat(2,1fr)}}
@page{margin:16mm 14mm}
@media print{body{background:#fff;font-size:12.5px}.bar{display:none}.page{margin:0;border:0;max-width:none;padding:0;border-top:0}
h2{break-after:avoid}.tile,.card,pre{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render_html(d: dict) -> str:
    h: list[str] = []
    h.append(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code review report - {_e(d['project_id'])}</title><style>{_CSS}</style></head><body>
<div class="bar"><b>Code review report</b><span>To save a PDF: Print, then choose "Save as PDF" as the destination.</span>
<button onclick="window.print()">Print / Save as PDF</button></div>
<main class="page">
<h1>Code review report</h1>
<div class="meta">Project <code>{_e(d['project_id'])}</code> &middot; generated {_e(d['generated'])} &middot; rule-based scanner (Python)</div>
<p class="headline">{_e(_headline(d))}</p>""")

    tot = max(d["total"], 1)
    h.append('<div class="tiles">'
             f'<div class="tile"><div class="n">{d["files_scanned"]}</div><div class="l">Python files scanned (of {d["files_total"]})</div></div>'
             + "".join(f'<div class="tile {s}"><div class="n">{d["by_severity"][s]}</div><div class="l">{SEV_LABEL[s]} severity</div></div>' for s in SEVERITIES)
             + "</div>")
    if d["total"]:
        h.append('<div class="stack" role="img" aria-label="Share of findings by severity">'
                 + "".join(f'<i class="{s}" style="width:{100 * d["by_severity"][s] / tot:.1f}%"></i>' for s in SEVERITIES) + "</div>")
    if d["languages"]:
        h.append('<div class="meta">Languages: ' + _e(", ".join(f"{k} ({v})" for k, v in d["languages"].items())) + "</div>")

    if d["findings"]:
        h.append("<h2>Findings</h2>")
        n = 0
        for sev in SEVERITIES:
            group = [f for f in d["findings"] if f["severity"] == sev]
            if not group:
                continue
            h.append(f'<h3>{SEV_LABEL[sev]} ({len(group)})</h3><p class="sevnote">{_e(SEV_MEANING[sev])}</p>')
            for f in group:
                n += 1
                h.append(f"""<div class="card {sev}"><div class="t">{n}. {_e(f['title'])}<span class="chip {sev}">{SEV_LABEL[sev]}</span><span class="chip">{_e(f['rule_id'])}</span></div>
<div class="loc">{_e(f['file_path'])}:{f['line']}</div><pre>{_e(f['code'].strip(chr(10)))}</pre>
<div class="row"><b>Why it matters.</b> {_e(f['explanation'])}</div>
<div class="row"><b>Suggested fix.</b> {_e(f['suggestion'])}</div></div>""")

    if d["per_file"]:
        h.append('<h2>By file</h2><table><thead><tr><th>File</th><th class="n">High</th><th class="n">Medium</th><th class="n">Low</th><th class="n">Total</th></tr></thead><tbody>')
        for r in d["per_file"]:
            h.append(f'<tr><td><code>{_e(r["file"])}</code></td><td class="n">{r["high"]}</td><td class="n">{r["medium"]}</td><td class="n">{r["low"]}</td><td class="n"><b>{r["total"]}</b></td></tr>')
        h.append("</tbody></table>")

    st = d["structure"]
    if st:
        h.append("<h2>Project structure</h2><ul>"
                 f"<li>{st['functions']} functions, {st['methods']} methods, {st['classes']} classes</li>"
                 f"<li>{st['call_edges']} call links and {st['import_edges']} import links between files</li>"
                 f"<li>Import cycles: {len(st['import_cycles']) or 'none'}</li></ul>")
        if st["import_cycles"]:
            h.append("<ul>" + "".join(f"<li><code>{_e(c)}</code></li>" for c in st["import_cycles"]) + "</ul>")
        if st["possibly_unused"]:
            shown = st["possibly_unused"][:40]
            h.append(f'<h3>Possibly unused ({len(st["possibly_unused"])})</h3><p class="sevnote">Nothing in the project visibly calls these. Check before deleting.</p><ul>'
                     + "".join(f"<li><code>{_e(u)}</code></li>" for u in shown)
                     + (f"<li>... and {len(st['possibly_unused']) - 40} more</li>" if len(st["possibly_unused"]) > 40 else "") + "</ul>")

    h.append("<h2>How to read this report</h2><ul>" + "".join(f"<li>{_e(t)}</li>" for t in LIMITS) + "</ul>")
    h.append(f'<div class="foot">Generated locally by AI Codebase Engineer. Your code was not sent anywhere. {_e(d["generated"])}</div></main></body></html>')
    return "".join(h)
