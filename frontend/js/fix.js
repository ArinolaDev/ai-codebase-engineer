// The fix workspace: a slide-over for one scanner finding. Propose -> verify -> retry -> pull request.
import { $, $$, esc, icon, api, runJob, progressPanel, toast, copy, sevLabel } from "./util.js";
import { diffView, verdictCard, verdictOf } from "./diff.js";
import { store } from "./app.js";

const RULE_FIXES = new Set(["BUG001", "SEC007", "SEC008", "SEC009"]);
const JS_RULE_FIXES = new Set(["SEC001", "SEC002", "SEC007", "SEC008", "BUG003", "BUG004", "BUG005"]);

export async function openFix(pid, f) {
  closeFix();
  const scrim = document.createElement("div"); scrim.className = "scrim"; scrim.id = "scrim";
  const dr = document.createElement("aside"); dr.className = "drawer"; dr.id = "drawer"; dr.setAttribute("role", "dialog"); dr.setAttribute("aria-label", "Fix workspace");
  const models = (store.system?.ollama?.models || []);
  const def = store.system?.ollama?.default_model || "llama3.2:1b";
  const isJs = !/\.py$/i.test(f.file_path);
  const ruleBased = (isJs ? JS_RULE_FIXES : RULE_FIXES).has(f.rule_id);
  dr.innerHTML = `
    <div class="drawer-h"><div><h2>${esc(f.title)}</h2><div class="row" style="gap:8px;margin-top:8px"><span class="pill ${f.severity}">${sevLabel[f.severity]}</span><span class="pill">${esc(f.rule_id)}</span><span class="loc">${esc(f.file_path)}:${f.line}</span></div></div>
      <button class="x" aria-label="Close" id="dr-x">${icon("x")}</button></div>
    <div class="drawer-b">
      <div class="callout"><h4>What's wrong</h4><p>${esc(f.explanation)}</p><h4 style="margin-top:8px">Suggested direction</h4><p>${esc(f.suggestion)}</p></div>
      <div><div class="muted" style="font-size:12.5px;margin-bottom:8px">Code around line ${f.line}</div><div class="code" id="ctx"><div class="ln"><span class="n"></span><span class="t muted">Loading…</span></div></div></div>

      <div class="card" style="border-radius:var(--r)"><div class="card-b" style="display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px">
        <div class="field"><label for="fx-model">Model for the fix${ruleBased ? " (only used if the rule fix can't apply)" : ""}</label>
          <select class="select" id="fx-model">${(models.length ? models : [def]).map((m) => `<option ${m.includes("qwen2.5-coder") ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
        <div class="field" ${isJs ? 'style="display:none"' : ""}><label>Proof</label><label class="row" style="gap:8px;color:var(--text-2);font-size:13.5px;min-height:40px"><input type="checkbox" id="fx-auto"> Let the model write tests for this function</label></div>
        <details style="grid-column:1/-1;${isJs ? "display:none" : ""}"><summary class="muted" style="cursor:pointer;font-size:13px">Use my own pytest code instead</summary>
          <textarea class="textarea" id="fx-tests" style="margin-top:10px" placeholder="import pytest&#10;from module import function&#10;&#10;def test_example():&#10;    assert function(1) == 1"></textarea></details>
      </div></div>

      <div class="actions-bar">
        <button class="act" data-kind="propose-fix"><b>${icon("wrench")}Preview fix</b><span>${ruleBased ? "Instant. A rule-based fix, checked for syntax." : "The model rewrites the function. Takes a minute or two."}</span></button>
        <button class="act" data-kind="verify-fix"><b>${icon("flask")}Verify with tests</b><span>${isJs ? "Checks the patch with the scanner. JavaScript tests can't run here yet." : "Run tests on a copy before and after the patch to catch regressions."}</span></button>
        <button class="act ${ruleBased ? "" : "rec"}" data-kind="fix-loop"><b>${icon("refresh")}Fix with retries</b><span>If a model's patch fails, the failure is fed back and it tries again, up to 3 times.</span></button>
      </div>
      <div id="run"></div><div id="out" style="display:flex;flex-direction:column;gap:18px"></div>
    </div>`;
  document.body.append(scrim, dr);
  const close = () => closeFix(); scrim.onclick = close; $("#dr-x", dr).onclick = close;
  document.addEventListener("keydown", escClose);

  // code context
  api(`/api/projects/${pid}/file?path=${encodeURIComponent(f.file_path)}`).then((r) => {
    const lines = r.content.split("\n"), a = Math.max(0, f.line - 9), b = Math.min(lines.length, f.line + 8);
    $("#ctx", dr).innerHTML = lines.slice(a, b).map((t, i) => { const n = a + i + 1; return `<div class="ln ${n === f.line ? "hl" : ""}"><span class="n">${n}</span><span class="t">${esc(t) || " "}</span></div>`; }).join("");
  }).catch(() => { $("#ctx", dr).innerHTML = `<div class="ln"><span class="n"></span><span class="t muted">Couldn't load the file.</span></div>`; });

  let running = false;
  $$(".act", dr).forEach((btn) => btn.onclick = async () => {
    if (running) return; running = true;
    const kind = btn.dataset.kind;
    $$(".act", dr).forEach((b) => b.disabled = true);
    $("#out", dr).innerHTML = "";
    const body = { file_path: f.file_path, line: f.line, rule_id: f.rule_id, model: $("#fx-model", dr).value };
    if (kind !== "propose-fix") { body.auto_tests = $("#fx-auto", dr).checked; const tc = $("#fx-tests", dr).value.trim(); if (tc) body.test_code = tc; }
    if (kind === "fix-loop") body.max_attempts = 3;
    const hints = { "propose-fix": "", "verify-fix": "Running tests twice, before and after the patch, in a temporary copy.", "fix-loop": "Each failed attempt teaches the next one. Local models take a minute or two per attempt." };
    const pp = progressPanel($("#run", dr), { "propose-fix": "Preparing a fix", "verify-fix": "Verifying the fix", "fix-loop": "Fixing with retries" }[kind], hints[kind]);
    try {
      const res = await runJob(pid, kind, body, pp.update);
      pp.end(true); render(kind, res, pid, f, dr);
    } catch (e) { pp.end(false, e.message); }
    running = false; $$(".act", dr).forEach((b) => b.disabled = false);
    $("#out", dr).scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function escClose(e) { if (e.key === "Escape") closeFix(); }
export function closeFix() {
  $("#scrim")?.remove(); $("#drawer")?.remove(); document.removeEventListener("keydown", escClose);
}

function render(kind, res, pid, f, dr) {
  const out = $("#out", dr);
  if (kind === "propose-fix") {
    const ok = res.verified;
    out.innerHTML = `<div class="verdict ${ok ? "untested" : "rejected"}"><div class="verdict-top"><span class="seal">${icon(ok ? "wrench" : "x")}</span><div><h3>${ok ? `Proposed ${res.method === "rule" ? "rule-based" : "model-written"} fix` : "This fix was rejected"}</h3><p>${esc(ok ? res.explanation || "" : "It failed the checks below, so it is not safe to apply. Try again, or use Fix with retries so the model learns from the failure.")}</p></div></div>
      ${checksHtml(res, ok)}</div><div id="d"></div><div id="im"></div><div id="pr"></div>`;
    $("#d", out).innerHTML = diffView(res.diff_structured, res.diff, `${f.rule_id}-fix.patch`);
    if (ok) {
      $("#im", out).innerHTML = impactHtml(res.impact);
      prPanel($("#pr", out), pid, f, res, "untested");
      out.insertAdjacentHTML("beforeend", `<div class="callout"><h4>Not proven yet</h4><p>This only checked that the file parses and the finding is gone. Use <b>Verify with tests</b> to prove it doesn't break anything.</p></div>`);
    }
    return;
  }
  if (kind === "verify-fix") { showVerify(out, res, pid, f, res.verdict, res); return; }
  // fix-loop
  const attempts = res.attempts || [];
  const best = res.best;
  const key = best ? best.verdict : res.outcome;
  out.innerHTML = `<div id="vc"></div>
    ${attempts.length > 1 ? `<div><div class="muted" style="font-size:12.5px;margin-bottom:8px">${attempts.length} attempts. ${esc(res.message || "")}</div><div class="attempts" id="att">${attempts.map((a, i) => `<button class="attempt" data-i="${i}" aria-pressed="${best && a.diff === best.diff}">${a.verdict === "safe_by_tests" ? icon("check", 14) : ""}Attempt ${a.number}<span class="pill ${a.verdict === "safe_by_tests" ? "ok" : a.verdict === "untested" ? "warn" : "bad"}">${esc(verdictOf(a.verdict)[1])}</span></button>`).join("")}</div></div>` : ""}
    <div id="body" style="display:flex;flex-direction:column;gap:18px"></div>`;
  const show = (a) => {
    const rr = best && a.diff === best.diff ? best : a;
    $("#vc", out).innerHTML = verdictCard(a.verdict || key, rr, null);
    const b = $("#body", out);
    b.innerHTML = `${a.feedback_given ? `<div class="callout"><h4>What this attempt was told</h4><p>${esc(a.feedback_given)}</p></div>` : ""}<div id="d"></div><div id="im"></div><div id="pr"></div>`;
    $("#d", b).innerHTML = diffView(a.diff_structured, a.diff, `${f.rule_id}-fix.patch`);
    if (a === best || (best && a.diff === best.diff)) $("#im", b).innerHTML = impactHtml(best?.impact);
    if (a.verdict !== "rejected") prPanel($("#pr", b), pid, f, a, a.verdict);
  };
  if (!attempts.length) { out.innerHTML = `<div class="callout"><h4>No fix</h4><p>${esc(res.message || "No attempt produced a patch.")}</p></div>`; return; }
  const start = attempts.find((a) => best && a.diff === best.diff) || attempts[attempts.length - 1];
  show(start);
  $$("#att .attempt", out).forEach((btn) => btn.onclick = () => { $$("#att .attempt", out).forEach((x) => x.setAttribute("aria-pressed", x === btn)); show(attempts[+btn.dataset.i]); });
}

function showVerify(out, res, pid, f, key, ev) {
  out.innerHTML = `${verdictCard(key, ev)}<div id="d"></div><div id="im"></div><div id="pr"></div>`;
  $("#d", out).innerHTML = diffView(res.diff_structured, res.diff, `${f.rule_id}-fix.patch`);
  $("#im", out).innerHTML = impactHtml(res.impact);
  if (key !== "rejected" && key !== "unsafe") prPanel($("#pr", out), pid, f, res, key);
  if (res.safety_note) out.insertAdjacentHTML("beforeend", `<p class="muted" style="font-size:12.5px">${esc(res.safety_note)}</p>`);
}
const checksHtml = (r, ok = true) => `<div class="evidence">${(r.checks || []).map((c) => `<div class="ev ${ok ? "pass" : "fail"}"><span class="i">${icon(ok ? "check" : "x")}</span><div>${esc(c.replace(/\.$/, ""))}</div></div>`).join("")}${(r.warnings || []).map((w) => `<div class="ev warn"><span class="i">${icon("alert")}</span><div>${esc(w)}</div></div>`).join("")}</div>`;
function impactHtml(im) {
  if (!im) return "";
  const rows = (im.direct_callers || []).map((c) => `<div class="mono" style="font-size:12.5px;color:var(--text-2)">${esc(c.node)} <span class="faint">line ${c.line}${c.approx ? " · approximate" : ""}</span></div>`).join("");
  return `<div class="callout"><h4>Blast radius</h4><p>${esc(im.summary)}</p>${rows}</div>`;
}

// ---- open a pull request ----
function prPanel(host, pid, f, r, verdict) {
  const ok = store.system?.github_configured;
  const diff = r.diff;
  if (!diff) return;
  host.innerHTML = `<details class="pr"><summary style="cursor:pointer;display:flex;gap:10px;align-items:center;font-weight:620">${icon("branch", 18)} Apply this fix</summary>
    <div style="display:flex;flex-direction:column;gap:14px;margin-top:14px">
      <div><div class="muted" style="font-size:12.5px;margin-bottom:6px">Apply it yourself. Save the patch as <span class="mono">fix.patch</span> in your repository, then run:</div>
        <div class="copy-line"><span>git apply fix.patch</span><button class="btn sm" data-c="git apply fix.patch">${icon("copy")}Copy</button></div></div>
      <hr style="border:0;border-top:1px solid var(--line);width:100%">
      <h3>Or open a pull request</h3>
      ${ok ? "" : `<div class="callout"><h4>GitHub isn't connected</h4><p>Create a fine-grained token with Contents and Pull requests (read and write), add <span class="mono">GITHUB_TOKEN=...</span> to <span class="mono">backend/.env</span> and restart the server.</p></div>`}
      <div class="field"><label>Repository</label><input class="input" id="pr-repo" placeholder="https://github.com/owner/repo" ${ok ? "" : "disabled"}></div>
      <div class="row"><div class="field grow"><label>Base branch (optional)</label><input class="input" id="pr-base" placeholder="default branch" ${ok ? "" : "disabled"}></div>
        <label class="row" style="gap:8px;color:var(--text-2);margin-top:20px"><input type="checkbox" id="pr-fork" ${ok ? "" : "disabled"}> Use a fork</label></div>
      <div class="row"><button class="btn" id="pr-dry" ${ok ? "" : "disabled"}>Check patch (nothing is written)</button><button class="btn primary" id="pr-go" ${ok ? "" : "disabled"}>Open pull request</button></div>
      <div id="pr-out"></div>
      ${verdict === "untested" ? `<p class="muted" style="font-size:12.5px">This fix hasn't been proven by tests. The pull request will say so.</p>` : ""}
    </div></details>`;
  $$("[data-c]", host).forEach((b) => b.onclick = () => copy(b.dataset.c));
  const send = async (dry) => {
    const repo = $("#pr-repo", host).value.trim(); if (!repo) return toast("Enter the repository URL", "", "err");
    if (!dry && !confirm("This publishes a branch and a pull request to GitHub. Continue?")) return;
    const o = $("#pr-out", host); o.innerHTML = `<div class="row muted"><span class="spin"></span>${dry ? "Checking against GitHub…" : "Opening pull request…"}</div>`;
    try {
      const res = await api(`/api/projects/${pid}/open-pr`, { method: "POST", body: { repo_url: repo, file_path: f.file_path, diff, base_branch: $("#pr-base", host).value.trim() || null, use_fork: $("#pr-fork", host).checked, dry_run: dry, confirm: !dry, rule_id: f.rule_id, explanation: r.explanation || r.summary || null, verdict, warnings: r.warnings || r.fix_warnings || [] } });
      o.innerHTML = res.pr_url
        ? `<div class="callout" style="border-color:#C4F54255"><h4>Pull request opened</h4><p><a href="${esc(res.pr_url)}" target="_blank" rel="noopener">${esc(res.pr_url)}</a></p></div>`
        : `<div class="callout"><h4>The patch applies cleanly</h4><p>It would create branch <span class="mono">${esc(res.new_branch)}</span> on ${esc(res.repo)} (base <span class="mono">${esc(res.base_branch)}</span>${res.uses_fork ? ", via a fork" : ""}).</p></div>`;
    } catch (e) { o.innerHTML = `<div class="callout" style="border-color:#FF6B6B55"><h4>Couldn't ${dry ? "check" : "open"} it</h4><p>${esc(e.message)}</p></div>`; }
  };
  $("#pr-dry", host).onclick = () => send(true); $("#pr-go", host).onclick = () => send(false);
}
