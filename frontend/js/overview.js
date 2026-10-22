import { esc, icon, api, $, LANG_COLORS, sevLabel } from "./util.js";

export async function renderOverview(host, { pid, st, go }) {
  const [graph] = await Promise.all([st.graph ? st.graph : api(`/api/projects/${pid}/graph`).catch(() => null)]);
  st.graph = graph;
  const s = st.summary, scan = st.scan;
  const langs = Object.entries(s.languages).sort((a, b) => b[1] - a[1]);
  const total = langs.reduce((n, [, c]) => n + c, 0) || 1;
  const fn = graph ? graph.stats.functions + graph.stats.methods : "–";

  host.innerHTML = `<div class="page">
  <div class="page-head"><div><h1>Overview</h1><p>What's in this project and what to do with it next.</p></div>
    <div class="actions"><a class="btn primary" href="#/p/${pid}/findings" data-scan>${icon("play")}${scan ? "Review findings" : "Scan for issues"}</a></div></div>

  <div class="stats">
    <div class="card stat"><span class="n">${s.file_count}</span><span class="l">Files</span></div>
    <div class="card stat"><span class="n">${fn}</span><span class="l">Functions and methods</span></div>
    <div class="card stat ${scan && scan.by_severity.high ? "red" : ""}"><span class="n">${scan ? scan.by_severity.high : "–"}</span><span class="l">High severity findings</span></div>
    <div class="card stat ${scan && scan.by_severity.medium ? "amber" : ""}"><span class="n">${scan ? scan.total_findings : "–"}</span><span class="l">${scan ? "Findings in total" : "Not scanned yet"}</span></div>
  </div>

  <div class="two">
    <div style="display:flex;flex-direction:column;gap:18px">
      <div class="card">
        <div class="card-h"><h2>Languages</h2><span class="sub">${langs.length} detected</span><span class="right muted" style="font-size:12.5px">Scanning: Python, JavaScript, TypeScript. Fixes and map: Python</span></div>
        <div class="card-b">
          <div class="lang-bar" role="img" aria-label="Language breakdown">${langs.map(([l, c], i) => `<i style="width:${(c / total) * 100}%;background:${LANG_COLORS[i % LANG_COLORS.length]}" title="${esc(l)} ${c}"></i>`).join("")}</div>
          <div class="lang-key">${langs.map(([l, c], i) => `<span><s style="background:${LANG_COLORS[i % LANG_COLORS.length]}"></s>${esc(l)} <b class="mono" style="color:var(--text)">${c}</b></span>`).join("")}</div>
        </div>
      </div>
      <div class="card">
        <div class="card-h"><h2>Next steps</h2></div>
        <div class="flow">
          ${step("shield", "Scan for security and bug patterns", "Fast, rule-based, no model needed. Finds SQL injection, weak hashing, bare excepts and more.", `#/p/${pid}/findings`, scan ? "Open" : "Scan", scan)}
          ${step("flask", "Generate and run tests for a function", "The model writes pytest cases; only the ones that pass are kept.", `#/p/${pid}/tests`, "Open")}
          ${step("graph", "See how the code connects", "Who calls what, what is probably unused, and what a change could break.", `#/p/${pid}/map`, "Open")}
          ${step("chat", "Ask questions about the code", st.indexed ? "Indexed and ready." : "Needs a one-time index (about a minute).", `#/p/${pid}/ask`, st.indexed ? "Ask" : "Set up", st.indexed)}
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-h"><h2>${scan ? "Most serious findings" : "Findings"}</h2></div>
      ${scan ? topFindings(scan, pid) : `<div class="empty"><h3>No scan yet</h3><p>Run the scanner to see issues ranked by severity. It takes a few seconds.</p><a class="btn primary" href="#/p/${pid}/findings">${icon("play")}Scan now</a></div>`}
    </div>
  </div></div>`;
}

function step(ic, title, desc, href, label, done) {
  return `<div class="flow-i"><div class="ic">${icon(ic)}</div><div><h4>${title}${done ? ` <span class="pill ok" style="margin-left:6px">Done</span>` : ""}</h4><p>${desc}</p></div><a class="btn sm" href="${href}">${label}</a></div>`;
}
function topFindings(scan, pid) {
  const top = scan.findings.slice(0, 6);
  if (!top.length) return `<div class="empty"><h3>Nothing flagged</h3><p>The scanner found no issues in ${scan.files_scanned} file(s). The model review can still catch logic bugs.</p></div>`;
  return `<div class="findings">${top.map((f) => `<a class="finding" href="#/p/${pid}/findings" style="color:inherit;text-decoration:none"><span class="bar ${f.severity}"></span>
    <div><div class="t">${esc(f.title)} <span class="pill ${f.severity}">${sevLabel[f.severity]}</span></div><div class="loc">${esc(f.file_path)}:${f.line}</div></div><span class="go">${icon("chev", 16)}</span></a>`).join("")}</div>`;
}
