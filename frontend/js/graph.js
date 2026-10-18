// Dependency map: force-directed SVG of files and functions. No libraries.
import { $, $$, esc, icon, api } from "./util.js";
import { openCode } from "./codeview.js";

const NS = "http://www.w3.org/2000/svg";
const C = { module: "#C4F542", function: "#6CB8FF", method: "#B692FF", class: "#FFB84D", unused: "#FFB84D", sel: "#FFFFFF" };

export async function renderMap(host, { pid, st }) {
  host.innerHTML = `<div class="page"><div class="page-head"><div><h1>Dependency map</h1><p>Files and the functions inside them. Lines show who calls whom. Click a function to see what a change to it could break.</p></div>
    <div class="actions"><button class="btn" id="fit">Fit to screen</button></div></div><div id="gb"><div class="row muted"><span class="spin"></span>Building the call graph…</div></div></div>`;
  let g;
  try { g = st.graph || (st.graph = await api(`/api/projects/${pid}/graph`)); }
  catch (e) { $("#gb", host).innerHTML = `<div class="card empty"><h3>Couldn't build the graph</h3><p>${esc(e.message)}</p></div>`; return; }
  if (!g.nodes.length) { $("#gb", host).innerHTML = `<div class="card empty"><h3>Nothing to map yet</h3><p>The graph needs Python files with functions.</p></div>`; return; }
  const unused = new Set(g.stats.possibly_unused);

  // visible nodes: modules + functions/methods (classes are folded into their methods)
  let nodes = g.nodes.filter((n) => n.kind !== "class");
  const linked = new Set(); g.edges.forEach((e) => { linked.add(e.source); linked.add(e.target); });
  let note = "";
  if (nodes.length > 320) { nodes = nodes.filter((n) => n.kind === "module" || linked.has(n.id)); note = "Large project: showing files and functions that take part in calls."; }
  const byId = new Map(nodes.map((n, i) => [n.id, Object.assign({ i }, n, { x: 0, y: 0, vx: 0, vy: 0, deg: 0 })]));
  const modOf = (n) => byId.get(g.nodes.find((m) => m.kind === "module" && m.file === n.file)?.id);
  const links = [];
  g.edges.forEach((e) => { const a = byId.get(e.source), b = byId.get(e.target); if (a && b && a !== b) { links.push({ a, b, kind: e.kind, approx: e.approx }); a.deg++; b.deg++; } });
  const ties = []; byId.forEach((n) => { if (n.kind !== "module") { const m = modOf(n); if (m) ties.push({ a: n, b: m }); } });
  const files = [...new Set(nodes.map((n) => n.file))];
  byId.forEach((n) => { const f = files.indexOf(n.file); const ang = (f / files.length) * Math.PI * 2; const r = 160 + Math.random() * 40; n.x = Math.cos(ang) * r + (Math.random() - .5) * 60; n.y = Math.sin(ang) * r + (Math.random() - .5) * 60; });
  const arr = [...byId.values()];
  const radius = (n) => (n.kind === "module" ? 13 : 6 + Math.min(8, n.deg * 1.6));

  $("#gb", host).innerHTML = `<div class="graph-wrap"><div class="graph-stage" id="stage"><svg id="svg" role="img" aria-label="Dependency graph"><defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#586075"/></marker><marker id="ar2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#C4F542"/></marker></defs><g id="vp"><g id="el"></g><g id="nl"></g></g></svg>
    <div class="legend"><span><i style="background:${C.module}"></i>File</span><span><i style="background:${C.function}"></i>Function</span><span><i style="background:${C.method}"></i>Method</span><span><i style="background:transparent;border:2px solid ${C.unused}"></i>Possibly unused</span></div></div>
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card" id="detail"><div class="card-b muted">${note ? esc(note) + " " : ""}Select a node to see its callers, what it calls and what depends on it.</div></div>
      <div class="card"><div class="card-h"><h3>Summary</h3></div><div class="card-b"><dl class="kv"><dt>Files</dt><dd>${g.stats.files}</dd><dt>Functions</dt><dd>${g.stats.functions + g.stats.methods}</dd><dt>Calls</dt><dd>${g.stats.call_edges}</dd><dt>Imports</dt><dd>${g.stats.import_edges}</dd><dt>Import cycles</dt><dd style="${g.stats.import_cycles.length ? "color:var(--red)" : ""}">${g.stats.import_cycles.length}</dd></dl></div></div>
      ${g.stats.possibly_unused.length ? `<div class="card"><div class="card-h"><h3>Possibly unused</h3><span class="sub">nothing calls these</span></div><div class="card-b side-list" style="padding:10px">${g.stats.possibly_unused.slice(0, 30).map((id) => `<button data-n="${esc(id)}">${esc(id.split("::").pop())}<span class="faint" style="margin-left:auto">${esc((id.split("::")[0] || "").split("/").pop())}</span></button>`).join("")}</div></div>` : ""}
      ${g.stats.most_called.length ? `<div class="card"><div class="card-h"><h3>Most called</h3></div><div class="card-b side-list" style="padding:10px">${g.stats.most_called.map((m) => `<button data-n="${esc(m.node)}">${esc(m.node.split("::").pop())}<span class="pill" style="margin-left:auto">${m.callers}</span></button>`).join("")}</div></div>` : ""}
    </div></div>`;

  const svg = $("#svg", host), vp = $("#vp", host), elG = $("#el", host), nlG = $("#nl", host);
  const mk = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const linkEls = links.map((l) => { const p = mk("line", { stroke: l.kind === "import" ? "#3A4258" : "#586075", "stroke-width": 1.3, "stroke-dasharray": l.kind === "import" ? "4 4" : l.approx ? "2 3" : "", "marker-end": "url(#ar)", opacity: .85 }); elG.appendChild(p); return p; });
  const tieEls = ties.map((t) => { const p = mk("line", { stroke: "#2E3546", "stroke-width": 1, opacity: .6 }); elG.insertBefore(p, elG.firstChild); return p; });
  const nodeEls = arr.map((n) => {
    const gEl = mk("g", { tabindex: 0, role: "button", "aria-label": `${n.kind} ${n.name}`, style: "cursor:pointer" });
    const isU = unused.has(n.id);
    gEl.appendChild(mk("circle", { r: radius(n), fill: n.kind === "module" ? "#C4F54222" : (C[n.kind] || C.function) + "33", stroke: isU ? C.unused : (C[n.kind] || C.function), "stroke-width": n.kind === "module" ? 2 : isU ? 2 : 1.6, "stroke-dasharray": isU ? "3 2" : "" }));
    const t = mk("text", { y: radius(n) + 13, "text-anchor": "middle", fill: n.kind === "module" ? "#E7EAF0" : "#B4BBCB", "font-size": n.kind === "module" ? 12.5 : 11, "font-family": "var(--mono)", "font-weight": n.kind === "module" ? 600 : 400, "pointer-events": "none" });
    t.textContent = n.kind === "module" ? n.file.split("/").pop() : n.name.split(".").pop();
    gEl.appendChild(t); nlG.appendChild(gEl); n.el = gEl; return gEl;
  });

  // physics
  let alpha = 1, raf = 0, drag = null;
  function tick() {
    const k = alpha;
    for (let i = 0; i < arr.length; i++) { const a = arr[i];
      for (let j = i + 1; j < arr.length; j++) { const b = arr[j]; let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy + 40; const f = (a.kind === "module" || b.kind === "module" ? 5200 : 2600) / d2; const d = Math.sqrt(d2); dx /= d; dy /= d; a.vx -= dx * f * k; a.vy -= dy * f * k; b.vx += dx * f * k; b.vy += dy * f * k; } }
    const spring = (A, B, len, str) => { const dx = B.x - A.x, dy = B.y - A.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - len) * str * k; const fx = dx / d * f, fy = dy / d * f; A.vx += fx; A.vy += fy; B.vx -= fx; B.vy -= fy; };
    links.forEach((l) => spring(l.a, l.b, l.kind === "import" ? 190 : 95, .045));
    ties.forEach((t) => spring(t.a, t.b, 85, .06));
    arr.forEach((n) => { n.vx -= n.x * .012 * k; n.vy -= n.y * .012 * k; if (n !== drag) { n.x += n.vx; n.y += n.vy; } n.vx *= .72; n.vy *= .72; });
    alpha *= .985; paint();
    if (alpha > .02 || drag) raf = requestAnimationFrame(tick); else raf = 0;
  }
  const kick = (a = .6) => { alpha = Math.max(alpha, a); if (!raf) raf = requestAnimationFrame(tick); };
  function paint() {
    links.forEach((l, i) => { const e = linkEls[i], dx = l.b.x - l.a.x, dy = l.b.y - l.a.y, d = Math.sqrt(dx * dx + dy * dy) || 1; e.setAttribute("x1", l.a.x + dx / d * radius(l.a)); e.setAttribute("y1", l.a.y + dy / d * radius(l.a)); e.setAttribute("x2", l.b.x - dx / d * (radius(l.b) + 3)); e.setAttribute("y2", l.b.y - dy / d * (radius(l.b) + 3)); });
    ties.forEach((t, i) => { const e = tieEls[i]; e.setAttribute("x1", t.a.x); e.setAttribute("y1", t.a.y); e.setAttribute("x2", t.b.x); e.setAttribute("y2", t.b.y); });
    arr.forEach((n) => n.el.setAttribute("transform", `translate(${n.x.toFixed(1)} ${n.y.toFixed(1)})`));
  }

  // viewport: pan / zoom
  let view = { x: 0, y: 0, k: 1 };
  const applyView = () => vp.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.k})`);
  const fit = () => { const r = svg.getBoundingClientRect(); if (!arr.length) return; const xs = arr.map((n) => n.x), ys = arr.map((n) => n.y); const minx = Math.min(...xs) - 70, maxx = Math.max(...xs) + 70, miny = Math.min(...ys) - 50, maxy = Math.max(...ys) + 60; const k = Math.min(r.width / (maxx - minx), r.height / (maxy - miny), 1.6); view = { k, x: r.width / 2 - ((minx + maxx) / 2) * k, y: r.height / 2 - ((miny + maxy) / 2) * k }; applyView(); };
  $("#fit", host).onclick = fit;
  const pt = (e) => { const r = svg.getBoundingClientRect(); return { x: (e.clientX - r.left - view.x) / view.k, y: (e.clientY - r.top - view.y) / view.k }; };
  let pan = null, moved = false;
  svg.addEventListener("pointerdown", (e) => { const g = e.target.closest("g[role=button]"); moved = false; svg.setPointerCapture(e.pointerId);
    if (g) { drag = arr[nodeEls.indexOf(g)]; kick(.3); } else pan = { x: e.clientX - view.x, y: e.clientY - view.y }; });
  svg.addEventListener("pointermove", (e) => { moved = true; if (drag) { const p = pt(e); drag.x = p.x; drag.y = p.y; drag.vx = drag.vy = 0; kick(.3); } else if (pan) { view.x = e.clientX - pan.x; view.y = e.clientY - pan.y; applyView(); } });
  svg.addEventListener("pointerup", (e) => { const was = drag; drag = null; pan = null; if (was && !moved) select(was); else if (!was && !moved) select(null); });
  svg.addEventListener("wheel", (e) => { e.preventDefault(); const r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, f = e.deltaY < 0 ? 1.12 : .89, k = Math.min(3, Math.max(.2, view.k * f)); view.x = mx - (mx - view.x) * (k / view.k); view.y = my - (my - view.y) * (k / view.k); view.k = k; applyView(); }, { passive: false });
  nodeEls.forEach((el, i) => el.addEventListener("keydown", (e) => { if (e.key === "Enter") select(arr[i]); }));

  // selection + detail
  async function select(n) {
    nodeEls.forEach((el) => el.style.opacity = n ? .28 : 1);
    linkEls.forEach((l) => { l.style.opacity = n ? .12 : .85; l.setAttribute("stroke", l.dataset.hl ? C.module : l.getAttribute("stroke")); });
    $$(".sel-ring", nlG).forEach((r) => r.remove());
    const box = $("#detail", host);
    if (!n) { box.innerHTML = `<div class="card-b muted">Select a node to see its callers, what it calls and what depends on it.</div>`; return; }
    const rel = new Set([n]); const hot = [];
    links.forEach((l, i) => { if (l.a === n || l.b === n) { rel.add(l.a); rel.add(l.b); linkEls[i].style.opacity = 1; linkEls[i].setAttribute("stroke", C.module); linkEls[i].setAttribute("marker-end", "url(#ar2)"); } else linkEls[i].setAttribute("marker-end", "url(#ar)"); });
    rel.forEach((m) => m.el.style.opacity = 1);
    n.el.insertBefore(mk("circle", { class: "sel-ring", r: radius(n) + 5, fill: "none", stroke: C.sel, "stroke-width": 1.5 }), n.el.firstChild);
    const isFn = n.kind === "function" || n.kind === "method";
    box.innerHTML = `<div class="card-h"><h3 style="word-break:break-all">${esc(n.name)}</h3><span class="pill ${n.kind === "module" ? "accent" : ""}">${esc(n.kind)}</span></div>
      <div class="card-b" style="display:flex;flex-direction:column;gap:14px"><dl class="kv"><dt>File</dt><dd title="${esc(n.file)}">${esc(n.file)}</dd>${isFn ? `<dt>Lines</dt><dd>${n.start}–${n.end}</dd>` : ""}${unused.has(n.id) ? `<dt>Status</dt><dd style="color:var(--amber)">Possibly unused</dd>` : ""}</dl>
      <div id="fn-info" class="muted" style="font-size:13px">${isFn ? "Loading impact…" : ""}</div>
      <button class="btn sm" id="view-src">View source</button></div>`;
    $("#view-src", box).onclick = () => openCode(pid, n.file, isFn ? n.start : null, isFn ? n.end : null, n.name);
    if (!isFn) return;
    try {
      const d = await api(`/api/projects/${pid}/graph/function?file=${encodeURIComponent(n.file)}&name=${encodeURIComponent(n.name)}&depth=3`);
      const li = (arr2, key) => arr2.length ? arr2.map((x) => `<button data-n="${esc(x[key] || x.node)}">${esc((x[key] || x.node).split("::").pop())}<span class="faint" style="margin-left:auto">${x.approx ? "approx." : "line " + x.line}</span></button>`).join("") : `<span class="faint" style="padding:0 9px;font-size:12.5px">None</span>`;
      const info = $("#fn-info", box); if (!info) return;
      info.innerHTML = `<div style="color:var(--text);margin-bottom:10px">${esc(d.summary)}</div>
        <div class="muted" style="font-size:12px;margin-bottom:4px">Called by</div><div class="side-list">${li(d.callers)}</div>
        <div class="muted" style="font-size:12px;margin:10px 0 4px">Calls</div><div class="side-list">${li(d.callees)}</div>
        ${d.impact.length ? `<div class="muted" style="font-size:12px;margin:10px 0 4px">Affected if changed</div><div class="side-list">${d.impact.map((x) => `<button data-n="${esc(x.node || x.id)}">${esc((x.node || x.id).split("::").pop())}<span class="faint" style="margin-left:auto">${x.depth != null ? x.depth + " hop" + (x.depth === 1 ? "" : "s") : ""}</span></button>`).join("")}</div>` : ""}`;
      wireList(info);
    } catch { const i = $("#fn-info", box); if (i) i.textContent = ""; }
  }
  const wireList = (root) => $$("[data-n]", root).forEach((b) => b.onclick = () => { const n = byId.get(b.dataset.n); if (n) select(n); });
  wireList(host);
  kick(1);
  setTimeout(fit, 450); setTimeout(fit, 1400);
  window.addEventListener("resize", fit, { once: true });
}
