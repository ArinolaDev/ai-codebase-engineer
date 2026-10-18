import { $, $$, esc, icon, api, toast } from "./util.js";
import { openCode } from "./codeview.js";

const SUGGEST = ["What does this project do?", "Where is user input handled?", "Which functions touch the database?", "How are errors handled?"];

export async function renderAsk(host, { pid, st, store }) {
  host.innerHTML = `<div class="page tight"><div class="page-head"><div><h1>Ask the code</h1><p>Answers come only from code retrieved from your project, with the exact lines cited.</p></div></div><div id="body"></div></div>`;
  const body = $("#body", host);
  if (!st.indexed) {   // the index may already exist from an earlier session: probe it
    try { const hits = await api(`/api/projects/${pid}/search?q=function&top_k=1`); if (hits.length) store.save(pid, "indexed", true); } catch {}
  }
  if (!st.indexed) return setup();
  chat();

  function setup() {
    body.innerHTML = `<div class="card empty"><h3>Index this project once</h3><p>The app reads every function and class and builds a local search index with free embeddings. It takes about a minute on first use and runs entirely on your machine.</p>
      <button class="btn primary" id="idx">${icon("play")}Build the index</button><div id="idx-s"></div></div>`;
    $("#idx", body).onclick = async () => {
      const b = $("#idx", body); b.disabled = true; $("#idx-s", body).innerHTML = `<div class="row muted"><span class="spin"></span>Indexing. The first run also loads the embedding model.</div>`;
      try { const r = await api(`/api/projects/${pid}/index`, { method: "POST" }); store.save(pid, "indexed", true); toast("Index ready", `${r.chunks_indexed} chunks from ${r.files_processed} files`, "ok"); chat(); }
      catch (e) { $("#idx-s", body).innerHTML = `<div class="callout" style="border-color:#FF6B6B55"><h4>Indexing failed</h4><p>${esc(e.message)}</p></div>`; b.disabled = false; }
    };
  }

  function chat() {
    body.innerHTML = `<div class="chat"><div class="thread" id="thread" aria-live="polite"></div>
      <div class="suggest" id="sg" style="margin-bottom:12px">${SUGGEST.map((q) => `<button>${esc(q)}</button>`).join("")}</div>
      <form class="composer" id="cf"><input class="input" id="q" placeholder="Ask anything about this codebase" autocomplete="off" aria-label="Question"><button class="btn primary" id="send">${icon("send")}Ask</button></form></div>`;
    const thread = $("#thread", body);
    const draw = () => { thread.innerHTML = st.chat.length ? st.chat.map((m) => msgHtml(m, pid)).join("") : `<div class="muted" style="margin:auto;text-align:center;max-width:44ch">Ask a question to get started. Try one of the suggestions below.</div>`; $$("[data-src]", thread).forEach((c) => c.onclick = () => { const [f, a, b, n] = c.dataset.src.split("|"); openCode(pid, f, +a, +b, n); }); thread.scrollTop = thread.scrollHeight; };
    draw();
    const ask = async (q) => {
      if (!q.trim()) return;
      st.chat.push({ role: "user", text: q }); draw();
      thread.insertAdjacentHTML("beforeend", `<div class="msg" id="typing"><div class="bubble typing"><i></i><i></i><i></i></div></div>`); thread.scrollTop = thread.scrollHeight;
      $("#send", body).disabled = true;
      try { const r = await api(`/api/projects/${pid}/chat`, { method: "POST", body: { question: q, top_k: 5 } }); st.chat.push({ role: "bot", text: r.answer, sources: r.sources }); }
      catch (e) { st.chat.push({ role: "bot", text: e.message, error: true }); }
      $("#send", body).disabled = false; draw();
    };
    $("#cf", body).onsubmit = (e) => { e.preventDefault(); const i = $("#q", body); const q = i.value; i.value = ""; ask(q); };
    $$("#sg button", body).forEach((b) => b.onclick = () => ask(b.textContent));
    $("#q", body).focus();
  }
}

function fmt(text) {   // minimal markdown: ``` fences and `inline`
  const parts = String(text).split(/```(?:\w+)?\n?/);
  return parts.map((p, i) => (i % 2 ? `<pre><code>${esc(p.replace(/\n$/, ""))}</code></pre>` : esc(p).replace(/`([^`\n]+)`/g, "<code>$1</code>"))).join("");
}
const rel = (p, pid) => { const n = String(p).replace(/\\/g, "/"); const i = n.indexOf(`/${pid}/`); return i >= 0 ? n.slice(i + pid.length + 2) : n; };
function msgHtml(m, pid) {
  if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.text)}</div></div>`;
  const src = (m.sources || []).map((s) => { const f = rel(s.file_path, pid); return `<button class="chip" data-src="${esc(f)}|${s.start_line}|${s.end_line}|${esc(s.symbol_name)}">${esc(s.symbol_name)} · ${esc(f)}:${s.start_line}</button>`; }).join("");
  return `<div class="msg"><div class="bubble" ${m.error ? `style="border-color:#FF6B6B55"` : ""}>${fmt(m.text)}</div>${src ? `<div class="src" aria-label="Sources">${src}</div>` : ""}</div>`;
}
