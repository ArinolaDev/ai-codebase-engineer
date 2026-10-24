import { $, $$, esc, icon, api, runJob, progressPanel, toast, sevLabel, ago } from "./util.js";
import { openFix } from "./fix.js";

export async function renderFindings(host, ctx) {
  const { pid, st, store } = ctx;
  let tab = "scanner", sev = "all";
  host.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Findings</h1><p>Rule-based scans are exact. Model reviews find logic bugs but are opinions, so each one is a lead to check.</p></div>
      <div class="actions"><span class="seg" role="tablist"><button data-tab="scanner" aria-pressed="true">Scanner</button><button data-tab="review" aria-pressed="false">Model review</button><button data-tab="history" aria-pressed="false">History</button></span></div></div>
    <div id="pane"></div></div>`;
  $$("[data-tab]", host).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", host).forEach((x) => x.setAttribute("aria-pressed", x === b)); draw(); });
  const pane = $("#pane", host);
  const draw = () => (tab === "scanner" ? drawScanner() : tab === "history" ? drawHistory() : drawReview());

  // ----- scanner -----
  async function runScan() {
    const run = progressPanel($("#run", pane), "Scanning your code");
    try {
      const res = await runJob(pid, "scan", { min_severity: "low" }, run.update);
      run.end(true); store.save(pid, "scan", res);
      const h = res.history; if (h && !h.first) toast("Compared with your last scan", h.new || h.fixed ? `${h.fixed} fixed, ${h.new} new` : "No change", h.new ? "" : "ok");
      const badge = $("[data-badge]"); if (badge) { badge.textContent = res.total_findings || ""; badge.style.display = res.total_findings ? "" : "none"; }
      drawScanner(true);
    } catch (e) { run.end(false, e.message); }
  }
  function drawScanner(skipAuto) {
    const scan = st.scan;
    if (!scan) {
      pane.innerHTML = `<div id="run"></div>`;
      if (!skipAuto) runScan();
      return;
    }
    const list = scan.findings.filter((f) => sev === "all" || f.severity === sev);
    const byFile = {}; list.forEach((f) => (byFile[f.file_path] ||= []).push(f));
    pane.innerHTML = `<div id="run"></div>
      <div class="toolbar"><span class="seg">${[["all", "All", scan.total_findings], ["high", "High", scan.by_severity.high], ["medium", "Medium", scan.by_severity.medium], ["low", "Low", scan.by_severity.low]].map(([k, l, n]) => `<button data-sev="${k}" aria-pressed="${sev === k}">${l}<span class="c">${n}</span></button>`).join("")}</span>
        <span class="muted" style="font-size:13px">${scan.files_scanned} file${scan.files_scanned === 1 ? "" : "s"} scanned</span>
        <span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn sm" href="/api/projects/${pid}/report?format=html" target="_blank" rel="noopener" title="Opens a printable page: choose Save as PDF">${icon("download")}Report (PDF)</a>
          <a class="btn sm" href="/api/projects/${pid}/report?format=md" download title="Download as a Markdown file">${icon("download")}Markdown</a>
          <button class="btn sm" id="rescan">${icon("refresh")}Scan again</button></span></div>
      <div class="card">${list.length ? Object.entries(byFile).map(([file, fs]) => `<div class="file-group">${esc(file)}<span class="pill">${fs.length}</span></div><div class="findings">${fs.map((f, i) => row(f, scan.findings.indexOf(f))).join("")}</div>`).join("")
        : `<div class="empty"><h3>${scan.total_findings ? "No findings at this severity" : "Nothing flagged"}</h3><p>${scan.total_findings ? "Try a different filter." : "The scanner found no issues. Try the model review for logic bugs."}</p></div>`}</div>`;
    $$("[data-sev]", pane).forEach((b) => b.onclick = () => { sev = b.dataset.sev; drawScanner(true); });
    $("#rescan", pane).onclick = () => { store.save(pid, "scan", null); drawScanner(); };
    $$(".finding", pane).forEach((el) => el.onclick = () => openFix(pid, scan.findings[+el.dataset.i]));
  }
  const row = (f, i) => `<button class="finding" data-i="${i}"><span class="bar ${f.severity}"></span>
    <div><div class="t">${esc(f.title)}<span class="pill ${f.severity}">${sevLabel[f.severity]}</span><span class="pill">${esc(f.rule_id)}</span></div>
    <div class="loc">line ${f.line}</div><div class="snip">${esc(f.code.trim())}</div></div><span class="go">${icon("chev", 18)}</span></button>`;

  // ----- history -----
  async function drawHistory() {
    pane.innerHTML = `<div class="card"><div class="empty"><div class="row muted"><span class="spin"></span>Loading</div></div></div>`;
    let list = [];
    try { list = await api(`/api/projects/${pid}/history`); }
    catch (e) { pane.innerHTML = `<div class="callout" style="border-color:#FF6B6B55"><h4>Couldn't load the history</h4><p>${esc(e.message)}</p></div>`; return; }
    if (!list.length) { pane.innerHTML = `<div class="card"><div class="empty"><h3>No scans saved yet</h3><p>Every scan you run is saved here, so you can see what you fixed and what is new. Run one on the Scanner tab.</p></div></div>`; return; }
    const max = Math.max(1, ...list.map((x) => x.total));
    const seg = (n, c) => (n ? `<i style="flex:${n};background:var(--${c})"></i>` : "");
    const change = (x) => x.first ? `<span class="pill">First scan</span>`
      : (x.new || x.fixed) ? `${x.fixed ? `<span class="pill ok">${x.fixed} fixed</span>` : ""}${x.new ? `<span class="pill high">${x.new} new</span>` : ""}` : `<span class="pill">No change</span>`;
    pane.innerHTML = `<div class="card"><div class="card-h"><h2>Scan history</h2><span class="sub">${list.length} scan${list.length === 1 ? "" : "s"}, newest first. Each is compared with the one before it.</span>
        <span class="right"><button class="btn sm" id="hist-clear">Clear history</button></span></div>
      <div id="hist">${list.map((x) => `<div class="hist-row" data-id="${x.id}" style="border-top:1px solid var(--line)">
        <button class="hist-b" data-id="${x.id}" style="all:unset;box-sizing:border-box;width:100%;cursor:pointer;display:grid;grid-template-columns:150px minmax(120px,1fr) 70px auto;gap:16px;align-items:center;padding:14px 20px" aria-expanded="false">
          <span title="${esc(new Date(x.at).toLocaleString())}"><b style="font-weight:600">${esc(ago(new Date(x.at).getTime() / 1000))}</b><br><span class="muted" style="font-size:12px">${x.files_scanned} file${x.files_scanned === 1 ? "" : "s"}</span></span>
          <span style="display:flex;height:8px;border-radius:99px;overflow:hidden;background:var(--panel-2);width:${Math.max(6, Math.round((x.total / max) * 100))}%;min-width:6px">${seg(x.by_severity.high, "red")}${seg(x.by_severity.medium, "amber")}${seg(x.by_severity.low, "blue")}</span>
          <span style="font:600 13px var(--mono)">${x.total}</span>
          <span style="display:flex;gap:6px;justify-content:flex-end">${change(x)}</span></button>
        <div class="hist-d" style="display:none;padding:0 20px 16px"></div></div>`).join("")}</div></div>`;
    $("#hist-clear", pane).onclick = async () => {
      if (!confirm("Delete the saved scan history for this project? Your code and findings are not affected.")) return;
      try { await api(`/api/projects/${pid}/history`, { method: "DELETE" }); toast("History cleared", "", "ok"); drawHistory(); } catch (e) { toast("Couldn't clear", e.message); }
    };
    $$(".hist-b", pane).forEach((b) => b.onclick = async () => {
      const box = $(".hist-d", b.parentElement), open = b.getAttribute("aria-expanded") === "true";
      b.setAttribute("aria-expanded", !open); box.style.display = open ? "none" : "block";
      if (open || box.dataset.loaded) return;
      box.innerHTML = `<div class="row muted"><span class="spin"></span>Loading</div>`;
      try {
        const d = await api(`/api/projects/${pid}/history/${b.dataset.id}`);
        const item = (f, kind) => `<div style="display:grid;grid-template-columns:4px minmax(0,1fr);gap:12px;padding:8px 0"><span class="bar ${f.severity}" style="border-radius:2px"></span><div style="min-width:0"><div style="font-weight:600;font-size:13.5px">${esc(f.title)} <span class="pill">${esc(f.rule_id)}</span></div><div class="muted" style="font:12px var(--mono);margin:2px 0 4px">${esc(f.file_path)}:${f.line}</div><div style="font:12.5px var(--mono);color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap${kind === "fixed" ? ";text-decoration:line-through;opacity:.7" : ""}">${esc((f.code || "").trim())}</div></div></div>`;
        const group = (title, arr, kind, none) => `<div style="margin-top:12px"><div style="font:600 12px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:4px">${title} (${arr.length})</div>${arr.length ? arr.map((f) => item(f, kind)).join("") : `<div class="muted" style="font-size:13px">${none}</div>`}</div>`;
        box.innerHTML = d.first ? `<div class="muted" style="font-size:13px">This was the first saved scan, so there is nothing to compare it with. ${d.total} finding${d.total === 1 ? "" : "s"} were recorded.</div>`
          : group("Fixed since the previous scan", d.fixed_findings, "fixed", "Nothing was fixed.") + group("New since the previous scan", d.new_findings, "new", "No new findings.")
            + `<div class="muted" style="font-size:12.5px;margin-top:10px">${d.unchanged} finding${d.unchanged === 1 ? "" : "s"} unchanged.</div>`;
        box.dataset.loaded = "1";
      } catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
    });
  }

  // ----- model review -----
  async function drawReview() {
    let files = [];
    try { files = (await api(`/api/projects/${pid}/files`)).filter((f) => f.language === "python"); } catch {}
    const models = store.system?.ollama?.models || [];
    pane.innerHTML = `<div class="card"><div class="card-b" style="display:grid;grid-template-columns:2fr 1.2fr 1.4fr;gap:14px;align-items:end">
      <div class="field"><label for="rv-file">File</label><select class="select" id="rv-file">${files.map((f) => `<option>${esc(f.path)}</option>`).join("") || "<option value=''>No Python files</option>"}</select></div>
      <div class="field"><label for="rv-fn">Function (optional)</label><input class="input" id="rv-fn" placeholder="all, up to 8"></div>
      <div class="field"><label for="rv-model">Model</label><select class="select" id="rv-model">${(models.length ? models : [store.system?.ollama?.default_model || "llama3.2:1b"]).map((m) => `<option ${m.includes("qwen2.5-coder") ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
      <label class="row" style="gap:8px;color:var(--text-2);grid-column:1/3"><input type="checkbox" id="rv-ctx"> Give the model call-graph context (who calls each function)</label>
      <button class="btn primary" id="rv-go" ${files.length ? "" : "disabled"}>${icon("play")}Review with model</button></div></div>
      <div id="run" style="margin-top:16px"></div><div id="rv-out" style="margin-top:16px">${st.review ? reviewHtml(st.review) : ""}</div>`;
    $("#rv-go", pane).onclick = async () => {
      const b = $("#rv-go", pane); b.disabled = true;
      const run = progressPanel($("#run", pane), "Reviewing with the local model", "A 3B model on a laptop CPU takes about a minute per function.");
      try {
        const body = { file_path: $("#rv-file", pane).value, model: $("#rv-model", pane).value, use_context: $("#rv-ctx", pane).checked };
        const fn = $("#rv-fn", pane).value.trim(); if (fn) body.function_name = fn;
        const res = await runJob(pid, "review", body, run.update);
        run.end(true); st.review = res; $("#rv-out", pane).innerHTML = reviewHtml(res);
      } catch (e) { run.end(false, e.message); }
      b.disabled = false;
    };
  }
  const reviewHtml = (r) => `<div class="card"><div class="card-h"><h2>Model findings</h2><span class="sub">${r.findings.length} kept after checking each quoted line exists</span></div>
    ${r.findings.length ? `<div class="findings">${r.findings.map((f) => `<div class="finding" style="cursor:default;grid-template-columns:4px minmax(0,1fr)"><span class="bar ${f.severity}"></span><div><div class="t">${esc(f.issue)}<span class="pill ${f.severity}">${sevLabel[f.severity] || esc(f.severity)}</span><span class="pill warn">Unverified</span></div><div class="loc">${esc(f.file_path)}:${f.line} in ${esc(f.function)}</div><div class="snip">${esc(f.code.trim())}</div></div></div>`).join("")}</div>`
      : `<div class="empty"><h3>No logic bugs reported</h3><p>The model didn't flag anything it could quote. That's not proof the code is correct.</p></div>`}
    <div class="card-b" style="border-top:1px solid var(--line);color:var(--muted);font-size:13px">${esc(r.note)} Fixes are available for scanner findings; use the Test lab to write tests for a function you suspect.</div></div>`;

  draw();
}
