import { $, esc, icon, api, toast, ago, LANG_COLORS } from "./util.js";
import { store, rememberProject, go } from "./app.js";

export async function renderHome(app) {
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem("ace:recent") || "[]"); } catch {}
  app.innerHTML = `<div class="home">
  <section class="home-l">
    <a class="brand" href="#/" style="padding:0"><span class="mark">${icon("code")}</span>Codebase Engineer</a>
    <div style="display:flex;flex-direction:column;gap:16px">
      <h1>Understand, test and fix any codebase.</h1>
      <p class="lede">Drop in a project. Find bugs and security holes, see how the code connects, ask questions in plain English, and get fixes that are proven by tests before you apply them.</p>
    </div>
    <div style="display:flex;flex-direction:column;gap:16px;max-width:520px">
      <label class="drop" id="drop" tabindex="0">
        <input type="file" id="file" accept=".zip">
        ${icon("upload")}
        <strong id="drop-t">Drop a .zip of your project</strong>
        <span class="muted" style="font-size:13px">or click to choose a file</span>
      </label>
      <div class="or">or clone from GitHub</div>
      <form class="row" id="git" style="flex-wrap:nowrap">
        <input class="input" id="git-url" placeholder="https://github.com/owner/repo" aria-label="Repository URL" autocomplete="off">
        <button class="btn" type="submit" id="git-go">Clone</button>
      </form>
    </div>
    <div class="capabilities">
      ${["Pattern scanner for security and bug rules", "Model review for logic bugs", "Fixes proven by running tests", "Live call graph and blast radius", "Chat grounded in your code", "Open a pull request with the fix"].map((t) => `<div>${icon("check")}<span>${t}</span></div>`).join("")}
    </div>
  </section>
  <section class="home-r">
    <h2>Recent projects</h2>
    <div class="proj-list" id="recent"></div>
  </section>
  </div>`;

  const drop = $("#drop"), input = $("#file");
  const busy = (on, label) => { $("#drop-t").textContent = label; drop.style.pointerEvents = on ? "none" : ""; drop.style.opacity = on ? ".7" : ""; };
  async function upload(f) {
    if (!f || !f.name.toLowerCase().endsWith(".zip")) return toast("Zip files only", "Compress your project folder into a .zip first.", "err");
    busy(true, `Uploading ${f.name}…`);
    try {
      const form = new FormData(); form.append("file", f);
      const p = await api("/api/projects/upload", { method: "POST", form });
      rememberProject(p); store.get(p.project_id).summary = p; go(`#/p/${p.project_id}/overview`);
    } catch (e) { toast("Upload failed", e.message, "err"); busy(false, "Drop a .zip of your project"); }
  }
  input.onchange = () => upload(input.files[0]);
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));

  $("#git").onsubmit = async (e) => {
    e.preventDefault();
    const url = $("#git-url").value.trim(); if (!url) return;
    const b = $("#git-go"); b.disabled = true; b.innerHTML = `<span class="spin"></span>Cloning`;
    try { const p = await api("/api/projects/from-git", { method: "POST", body: { repo_url: url } }); rememberProject(p); store.get(p.project_id).summary = p; go(`#/p/${p.project_id}/overview`); }
    catch (err) { toast("Clone failed", err.message, "err"); b.disabled = false; b.textContent = "Clone"; }
  };

  // merge server list (survives reloads) with local memory
  try {
    const server = await api("/api/projects");
    const seen = new Set(server.map((p) => p.project_id));
    recent = [...server, ...recent.filter((p) => !seen.has(p.project_id))];
  } catch {}
  const host = $("#recent");
  if (!recent.length) {
    host.innerHTML = `<div class="empty" style="padding:8px 0"><h3>No projects yet</h3><p>Upload a zip on the left. It takes a few seconds and nothing leaves your computer.</p></div>`;
  } else host.innerHTML = recent.slice(0, 12).map((p) => {
    const langs = Object.entries(p.languages || {}).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([l, n]) => `${l} ${n}`).join(", ");
    return `<a class="proj-row" href="#/p/${esc(p.project_id)}/overview"><span class="glyph">${esc(p.project_id.slice(0, 2))}</span>
      <span class="meta"><b>${esc(p.project_id)}</b><span>${p.file_count} files${langs ? " · " + esc(langs) : ""}${p.created ? " · " + ago(p.created) : ""}</span></span><span class="chev">${icon("chev", 18)}</span></a>`;
  }).join("");
}
