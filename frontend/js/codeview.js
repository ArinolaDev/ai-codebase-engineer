// Read-only source viewer in a slide-over (used by chat sources and the dependency map).
import { $, esc, icon, api, toast } from "./util.js";
import { closeFix } from "./fix.js";

export async function openCode(pid, path, start, end, title) {
  closeFix();
  const scrim = document.createElement("div"); scrim.className = "scrim"; scrim.id = "scrim";
  const dr = document.createElement("aside"); dr.className = "drawer"; dr.id = "drawer"; dr.setAttribute("role", "dialog");
  dr.innerHTML = `<div class="drawer-h"><div><h2>${esc(title || path)}</h2><div class="loc">${esc(path)}${start ? `:${start}${end && end !== start ? "–" + end : ""}` : ""}</div></div><button class="x" aria-label="Close" id="dr-x">${icon("x")}</button></div>
    <div class="drawer-b"><div class="code" id="src" style="max-height:none"></div></div>`;
  document.body.append(scrim, dr);
  scrim.onclick = closeFix; $("#dr-x", dr).onclick = closeFix;
  try {
    const r = await api(`/api/projects/${pid}/file?path=${encodeURIComponent(path)}`);
    $("#src", dr).innerHTML = r.content.split("\n").map((t, i) => { const n = i + 1; const hl = start && n >= start && n <= (end || start); return `<div class="ln ${hl ? "hl" : ""}"><span class="n">${n}</span><span class="t">${esc(t) || " "}</span></div>`; }).join("");
    if (start) setTimeout(() => $("#src .hl", dr)?.scrollIntoView({ block: "center" }), 30);
  } catch (e) { $("#src", dr).innerHTML = `<div class="ln"><span class="n"></span><span class="t muted">${esc(e.message)}</span></div>`; }
}
