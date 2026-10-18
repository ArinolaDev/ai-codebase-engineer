// App shell + hash router. Routes: #/  (home)   #/p/<id>/<overview|findings|tests|map|ask>
import { $, $$, esc, icon, api, toast } from "./util.js";
import { renderHome } from "./home.js";
import { renderOverview } from "./overview.js";
import { renderFindings } from "./findings.js";
import { renderTests } from "./tests.js";
import { renderMap } from "./graph.js";
import { renderAsk } from "./chat.js";

export const store = {            // per-project, in-memory (scan results survive reloads via sessionStorage)
  projects: {},
  get(pid) { return (this.projects[pid] ||= { scan: load(`scan:${pid}`), review: null, graph: null, indexed: load(`idx:${pid}`) || false, chat: [], summary: null }); },
  save(pid, key, val) { this.get(pid)[key] = val; save(`${key === "indexed" ? "idx" : key}:${pid}`, val); },
  system: null,
};
function load(k) { try { return JSON.parse(sessionStorage.getItem("ace:" + k)); } catch { return null; } }
function save(k, v) { try { sessionStorage.setItem("ace:" + k, JSON.stringify(v)); } catch {} }

export function rememberProject(p) {
  try {
    const list = JSON.parse(localStorage.getItem("ace:recent") || "[]").filter((x) => x.project_id !== p.project_id);
    list.unshift({ project_id: p.project_id, file_count: p.file_count, languages: p.languages, created: Math.floor(Date.now() / 1000) });
    localStorage.setItem("ace:recent", JSON.stringify(list.slice(0, 12)));
  } catch {}
}
export const go = (hash) => { location.hash = hash; };

const VIEWS = [
  ["overview", "Overview", "grid", renderOverview],
  ["findings", "Findings", "shield", renderFindings],
  ["tests", "Test lab", "flask", renderTests],
  ["map", "Dependency map", "graph", renderMap],
  ["ask", "Ask the code", "chat", renderAsk],
];

async function refreshStatus() {
  try { store.system = await api("/api/system/status"); } catch { store.system = null; }
  const el = $("#sys-status"); if (el) el.innerHTML = statusHtml();
}
export const preferred = (ms) => ms.find((m) => /coder/i.test(m)) || ms[0];
function statusHtml() {
  const s = store.system;
  if (!s) return `<div class="row"><span class="dot bad"></span>Backend unreachable</div>`;
  const o = s.ollama;
  return `<div class="row"><span class="dot ${o.running ? "ok" : "bad"}"></span>${o.running ? "Ollama running locally" : "Ollama not running"}</div>
    <div class="mono" style="font-size:12.5px;color:var(--text)">${esc(preferred(o.models) || o.default_model)}${o.models.length > 1 ? ` <span class="faint">+${o.models.length - 1}</span>` : ""}</div>
    <div class="muted" style="font-size:12px;line-height:1.45">${o.running ? "Your code never leaves this machine. No API key, no cost." : "Start the Ollama app so chat, reviews and model fixes work."}</div>
    <div class="row" style="font-size:12.5px;color:var(--text-2)"><span class="dot ${s.github_configured ? "ok" : ""}"></span>GitHub ${s.github_configured ? "connected" : "not set up"}</div>`;
}

let current = null;
async function route() {
  document.body.classList.remove("nav-open");
  const m = location.hash.match(/^#\/p\/([^/]+)\/?([a-z]*)/);
  const app = $("#app");
  if (!m) { current = null; renderHome(app); return; }
  const pid = m[1], view = VIEWS.find((v) => v[0] === m[2]) || VIEWS[0];
  const st = store.get(pid);
  if (!st.summary) {
    try { st.summary = await api(`/api/projects/${pid}/summary`); rememberProject(st.summary); }
    catch (e) { toast("Project not found", "The server may have restarted. Upload the project again.", "err"); try { localStorage.setItem("ace:recent", JSON.stringify(JSON.parse(localStorage.getItem("ace:recent") || "[]").filter((x) => x.project_id !== pid))); } catch {} go("#/"); return; }
  }
  if (!current || current.pid !== pid) {
    app.innerHTML = shell(pid, st);
    $("#menu").onclick = () => document.body.classList.toggle("nav-open");
    refreshStatus();
  }
  current = { pid, view: view[0] };
  const found = st.scan ? st.scan.total_findings : null;
  $$(".nav a").forEach((a) => a.setAttribute("aria-current", a.dataset.v === view[0] ? "page" : "false"));
  const badge = $("[data-badge]"); if (badge) { badge.textContent = found || ""; badge.style.display = found ? "" : "none"; }
  $("#crumb").textContent = view[1];
  const host = $("#view"); host.innerHTML = "";
  host.scrollTo?.(0, 0); window.scrollTo(0, 0);
  try { await view[3](host, { pid, st, store, go }); }
  catch (e) { host.innerHTML = `<div class="page"><div class="card empty"><h3>Something went wrong</h3><p>${esc(e.message)}</p></div></div>`; }
}

function shell(pid, st) {
  return `<div class="shell">
  <aside class="rail" aria-label="Main">
    <a class="brand" href="#/"><span class="mark">${icon("code")}</span>Codebase Engineer</a>
    <nav class="nav"><div class="nav-h">Workspace</div>
      ${VIEWS.map(([k, label, ic]) => `<a href="#/p/${pid}/${k}" data-v="${k}">${icon(ic)}${label}${k === "findings" ? `<span class="count" data-badge style="display:none"></span>` : ""}</a>`).join("")}
    </nav>
    <div class="rail-foot">
      <div class="proj-card"><span class="t">Project</span><span class="id" title="${esc(pid)}">${esc(pid)}</span><span class="t">${st.summary.file_count} files · <a href="#/">switch</a></span></div>
      <div class="status" id="sys-status">${statusHtml()}</div>
    </div>
  </aside>
  <div class="main">
    <div class="topbar"><button class="btn ghost sm menu-btn" id="menu" aria-label="Menu">${icon("menu")}</button>
      <div class="crumbs"><a href="#/" style="color:var(--muted)">Projects</a><span class="faint">/</span><span class="mono">${esc(pid.slice(0, 8))}</span><span class="faint">/</span><b id="crumb"></b></div></div>
    <main id="view"></main>
  </div></div>`;
}

window.addEventListener("hashchange", route);
route();
