import { $, esc, icon, api, toast, copy, download } from "./util.js";

export async function renderTests(host, { pid, st }) {
  let parsed = [];
  try { parsed = (await api(`/api/projects/${pid}/parse`)).filter((f) => f.functions.some((fn) => !fn.parent_class)); } catch {}
  host.innerHTML = `<div class="page">
    <div class="page-head"><div><h1>Test lab</h1><p>The model writes pytest cases for one function. Run them to keep only the tests that pass, then use those as proof when you verify a fix.</p></div></div>
    <div class="card"><div class="card-b" style="display:grid;grid-template-columns:1.6fr 1.2fr;gap:14px;align-items:end">
      <div class="field"><label for="t-file">File</label><select class="select" id="t-file">${parsed.map((f) => `<option>${esc(f.path)}</option>`).join("") || "<option value=''>No Python functions found</option>"}</select></div>
      <div class="field"><label for="t-fn">Function</label><select class="select" id="t-fn"></select></div>
      <label class="row" style="gap:8px;color:var(--text-2);grid-column:1/-1"><input type="checkbox" id="t-run" checked> Run the tests and keep only the passing ones <span class="faint" style="font-size:12.5px">(this executes code from the project, so use it on projects you trust)</span></label>
      <div><button class="btn primary" id="t-go" ${parsed.length ? "" : "disabled"}>${icon("flask")}Generate tests</button></div></div></div>
    <div id="t-out" style="margin-top:18px"></div></div>`;
  const fileSel = $("#t-file", host), fnSel = $("#t-fn", host);
  const fill = () => { const f = parsed.find((x) => x.path === fileSel.value); fnSel.innerHTML = (f ? f.functions.filter((fn) => !fn.parent_class) : []).map((fn) => `<option>${esc(fn.name)}</option>`).join(""); };
  fileSel.onchange = fill; fill();
  $("#t-go", host).onclick = async () => {
    const b = $("#t-go", host), out = $("#t-out", host); b.disabled = true;
    out.innerHTML = `<div class="progress"><div class="progress-h"><span class="spin" style="color:var(--accent)"></span>Writing tests with the local model</div><div class="meter indet"><i></i></div><div class="hint" style="padding-top:10px">This usually takes under a minute. If it takes longer, check that Ollama is running.</div></div>`;
    try {
      const r = await api(`/api/projects/${pid}/generate-tests`, { method: "POST", body: { file_path: fileSel.value, function_name: fnSel.value, verify: $("#t-run", host).checked } });
      out.innerHTML = result(r);
      const keep = r.passing_code || r.generated_code;
      $("[data-copy]", out)?.addEventListener("click", () => copy(keep, "Tests copied"));
      $("[data-dl]", out)?.addEventListener("click", () => download(`test_${r.function}.py`, keep));
    } catch (e) { out.innerHTML = `<div class="callout" style="border-color:#FF6B6B55"><h4>Couldn't generate tests</h4><p>${esc(e.message)}</p></div>`; }
    b.disabled = false;
  };
}
function result(r) {
  const pass = r.passing_tests || [], fail = r.failing_tests || [];
  const code = r.passing_code || r.generated_code;
  return `<div style="display:flex;flex-direction:column;gap:16px">
    ${r.verified ? `<div class="verdict ${pass.length ? "safe" : "untested"}"><div class="verdict-top"><span class="seal">${icon(pass.length ? "check" : "alert")}</span><div><h3>${pass.length} passing, ${fail.length} failing</h3><p>Only the passing tests are kept below. A failing test means the test is wrong or the function has a real bug, so read the reason.</p></div></div>
      <div class="evidence">${pass.map((n) => `<div class="ev pass"><span class="i">${icon("check")}</span><div class="mono">${esc(n)}</div></div>`).join("")}${fail.map((t) => `<div class="ev fail"><span class="i">${icon("x")}</span><div class="mono">${esc(t.name)}<div class="d">${esc(t.reason)}</div></div></div>`).join("")}</div></div>`
      : `<div class="callout"><h4>${r.run_error ? "The tests couldn't run" : "Generated, not run"}</h4><p>${esc(r.run_error || r.note)}</p></div>`}
    <div class="card"><div class="card-h"><h3>${r.passing_code ? "Passing tests" : "Generated tests"}</h3><span class="sub mono">${esc(r.module)}.${esc(r.function)}</span><span class="right"><button class="btn sm" data-copy>${icon("copy")}Copy</button><button class="btn sm" data-dl>${icon("download")}Download</button></span></div>
      <div class="code" style="border:0;border-radius:0 0 var(--r-lg) var(--r-lg);max-height:480px">${code.split("\n").map((t, i) => `<div class="ln"><span class="n">${i + 1}</span><span class="t">${esc(t) || " "}</span></div>`).join("")}</div></div>
    ${/sys\.path\.insert/.test(code) ? `<p class="muted" style="font-size:12.5px">The <span class="mono">sys.path.insert</span> line lets the tests import your code from the server's copy. Change it to your own project path if you save these tests in your repository.</p>` : ""}</div>`;
}
