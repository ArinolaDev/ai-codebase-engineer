import { $, $$, esc, icon, api, runJob, progressPanel, toast, sevLabel } from "./util.js";
import { openFix } from "./fix.js";

export async function renderFindings(host, ctx) {
  const { pid, st, store } = ctx;
  let tab = "scanner", sev = "all";
  host.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Findings</h1><p>Rule-based scans are exact. Model reviews find logic bugs but are opinions, so each one is a lead to check.</p></div>
      <div class="actions"><span class="seg" role="tablist"><button data-tab="scanner" aria-pressed="true">Scanner</button><button data-tab="review" aria-pressed="false">Model review</button></span></div></div>
    <div id="pane"></div></div>`;
  $$("[data-tab]", host).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", host).forEach((x) => x.setAttribute("aria-pressed", x === b)); draw(); });
  const pane = $("#pane", host);
  const draw = () => (tab === "scanner" ? drawScanner() : drawReview());

  // ----- scanner -----
  async function runScan() {
    const run = progressPanel($("#run", pane), "Scanning your Python files");
    try {
      const res = await runJob(pid, "scan", { min_severity: "low" }, run.update);
      run.end(true); store.save(pid, "scan", res);
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
        <span class="muted" style="font-size:13px">${scan.files_scanned} Python file${scan.files_scanned === 1 ? "" : "s"} scanned</span>
        <button class="btn sm" id="rescan" style="margin-left:auto">${icon("refresh")}Scan again</button></div>
      <div class="card">${list.length ? Object.entries(byFile).map(([file, fs]) => `<div class="file-group">${esc(file)}<span class="pill">${fs.length}</span></div><div class="findings">${fs.map((f, i) => row(f, scan.findings.indexOf(f))).join("")}</div>`).join("")
        : `<div class="empty"><h3>${scan.total_findings ? "No findings at this severity" : "Nothing flagged"}</h3><p>${scan.total_findings ? "Try a different filter." : "The scanner found no issues. Try the model review for logic bugs."}</p></div>`}</div>`;
    $$("[data-sev]", pane).forEach((b) => b.onclick = () => { sev = b.dataset.sev; drawScanner(true); });
    $("#rescan", pane).onclick = () => { store.save(pid, "scan", null); drawScanner(); };
    $$(".finding", pane).forEach((el) => el.onclick = () => openFix(pid, scan.findings[+el.dataset.i]));
  }
  const row = (f, i) => `<button class="finding" data-i="${i}"><span class="bar ${f.severity}"></span>
    <div><div class="t">${esc(f.title)}<span class="pill ${f.severity}">${sevLabel[f.severity]}</span><span class="pill">${esc(f.rule_id)}</span></div>
    <div class="loc">line ${f.line}</div><div class="snip">${esc(f.code.trim())}</div></div><span class="go">${icon("chev", 18)}</span></button>`;

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
