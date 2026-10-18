// Shared helpers: HTTP, jobs (live progress), formatting, tiny icon set, toasts.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const PATHS = {
  code: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14"/>',
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  flask: '<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3"/>',
  graph: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8 7l8 1M7 8l4 8M17 10l-4 6"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  minus: '<path d="M6 12h12"/>',
  alert: '<path d="M12 8v5M12 16.5v.01"/><path d="M10.3 4l-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0z"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  branch: '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 7.2v9.6M18 11.2c0 4-6 3-12 6"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.5-3.5M4 4v4h4M4 13a8 8 0 0 0 14.5 3.5M20 20v-4h-4"/>',
  send: '<path d="M4 12l16-8-6 16-3-6.5z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
};
export const icon = (name, size) => `<svg viewBox="0 0 24 24" aria-hidden="true"${size ? ` width="${size}" height="${size}"` : ""}>${PATHS[name] || ""}</svg>`;

// ---------- HTTP ----------
export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

function explain(detail, status) {
  if (!detail) return `Request failed (${status}).`;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => `${(d.loc || []).slice(1).join(".") || "body"}: ${d.msg}`).join("; ");
  return JSON.stringify(detail);
}

export async function api(path, { method = "GET", body, form } = {}) {
  const opt = { method, headers: {} };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opt); }
  catch { throw new ApiError("Can't reach the backend. Is uvicorn still running in your terminal?", 0); }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(explain(data && data.detail, res.status), res.status);
  return data;
}

// ---------- jobs with live progress (Server-Sent Events) ----------
// onEvent({step,message,t,n,of}) is called for each progress event. Resolves with the job result.
export function runJob(pid, kind, body, onEvent = () => {}) {
  return new Promise(async (resolve, reject) => {
    let job;
    try { job = await api(`/api/projects/${pid}/jobs/${kind}`, { method: "POST", body: body || {} }); }
    catch (e) { return reject(e); }
    let finished = false, es;
    const finish = (fn, v) => { if (finished) return; finished = true; es && es.close(); fn(v); };
    const poll = async () => {            // fallback if the stream drops
      while (!finished) {
        await new Promise((r) => setTimeout(r, 1500));
        try {
          const s = await api(job.status_url);
          if (s.status === "done") return finish(resolve, s.result);
          if (s.status === "error") return finish(reject, new ApiError(s.error || "The job failed."));
        } catch (e) { return finish(reject, e); }
      }
    };
    es = new EventSource(job.events_url);
    es.addEventListener("progress", (m) => { try { onEvent(JSON.parse(m.data)); } catch {} });
    es.addEventListener("result", (m) => { try { finish(resolve, JSON.parse(m.data).result); } catch (e) { finish(reject, e); } });
    es.addEventListener("error", (m) => {
      if (m.data) { try { return finish(reject, new ApiError(JSON.parse(m.data).error || "The job failed.")); } catch {} }
      if (!finished && es.readyState === EventSource.CLOSED) poll();   // connection dropped, not a job error
    });
  });
}

// ---------- small bits ----------
export function toast(title, text = "", kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `<div><b>${esc(title)}</b>${text ? `<span>${esc(text)}</span>` : ""}</div>`;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "err" ? 8000 : 4000);
}
export async function copy(text, label = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch { toast("Couldn't copy", "Select the text and copy it manually.", "err"); }
}
export function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
export const sevLabel = { high: "High", medium: "Medium", low: "Low" };
export const ago = (ts) => {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 90) return "just now"; if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`; return `${Math.round(s / 86400)} d ago`;
};
export const LANG_COLORS = ["#C4F542", "#6CB8FF", "#FFB84D", "#B692FF", "#FF8FB1", "#5BD68A", "#8089A0"];

// Progress panel used by every long operation. Returns {update(ev), done(ok,msg), el}
export function progressPanel(host, title, hint = "") {
  host.innerHTML = `<div class="progress" role="status">
    <div class="progress-h"><span class="spin" style="color:var(--accent)"></span><span class="ttl">${esc(title)}</span><span class="muted mono" style="margin-left:auto;font-size:12px" data-t>0.0s</span></div>
    <div class="meter indet"><i></i></div>
    <div class="log" data-log></div>${hint ? `<div class="hint">${esc(hint)}</div>` : ""}</div>`;
  const log = $("[data-log]", host), meter = $(".meter", host), bar = $(".meter i", host), tEl = $("[data-t]", host);
  const started = Date.now();
  const tick = setInterval(() => { tEl.textContent = ((Date.now() - started) / 1000).toFixed(0) + "s"; }, 1000);
  return {
    update(ev) {
      const row = document.createElement("div");
      row.innerHTML = `<time>${(ev.t ?? 0).toFixed(1)}s</time><span>${esc(ev.message)}</span>`;
      log.appendChild(row); log.scrollTop = log.scrollHeight;
      if (ev.of) { meter.classList.remove("indet"); bar.style.width = Math.round(((ev.n - 1) / ev.of) * 100) + "%"; }
    },
    end(ok = true, msg = "") {
      clearInterval(tick);
      if (ok) { host.innerHTML = ""; return; }
      $(".spin", host).outerHTML = `<span style="color:var(--red);width:16px;height:16px;display:inline-grid">${icon("alert")}</span>`;
      $(".ttl", host).textContent = "Stopped";
      meter.remove();
      const r = document.createElement("div"); r.className = "err"; r.innerHTML = `<time></time><span>${esc(msg)}</span>`; log.appendChild(r);
    },
  };
}
