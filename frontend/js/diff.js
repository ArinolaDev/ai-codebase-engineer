// Diff viewer, evidence panel and patch actions. Works from the API's diff_structured.
import { esc, icon, $, $$, copy, download } from "./util.js";

export function diffView(d, rawDiff, filename = "fix.patch") {
  const files = (d && d.files) || [];
  if (!files.length) return `<div class="callout"><h4>No changes</h4><p>The proposed patch doesn't change the file.</p></div>`;
  const stats = d.stats || {};
  const id = "df" + Math.random().toString(36).slice(2, 8);
  const body = (split) => files.map((f) => f.hunks.map((h) => `<div class="hunk">${esc(h.header)}</div>` + (split ? splitRows(h) : unifiedRows(h))).join("")).join("");
  setTimeout(() => {
    const root = document.getElementById(id); if (!root) return;
    const b = $(".diff-body", root);
    $$("[data-mode]", root).forEach((btn) => btn.onclick = () => { $$("[data-mode]", root).forEach((x) => x.setAttribute("aria-pressed", x === btn)); b.innerHTML = body(btn.dataset.mode === "split"); });
    $("[data-copy]", root).onclick = () => copy(rawDiff, "Patch copied");
    $("[data-dl]", root).onclick = () => download(filename, rawDiff);
  }, 0);
  return `<div class="diff" id="${id}">
    <div class="diff-h"><span>${esc(files[0].new_path.replace(/^b\//, ""))}</span><span class="plus">+${stats.added ?? 0}</span><span class="minus">−${stats.removed ?? 0}</span>
      <span style="margin-left:auto" class="seg"><button data-mode="unified" aria-pressed="true">Unified</button><button data-mode="split" aria-pressed="false">Side by side</button></span>
      <button class="btn sm ghost" data-copy title="Copy patch">${icon("copy")}</button><button class="btn sm ghost" data-dl title="Download .patch">${icon("download")}</button></div>
    <div class="diff-body">${body(false)}</div></div>`;
}
const unifiedRows = (h) => h.lines.map((l) => `<div class="u ${l.type === "add" ? "add" : l.type === "del" ? "del" : ""}"><span class="a">${l.old_no ?? ""}</span><span class="b">${l.new_no ?? ""}</span><span class="m">${l.type === "add" ? "+" : l.type === "del" ? "−" : ""}</span><span class="c">${esc(l.text)}</span></div>`).join("");
function half(x, side) {
  if (!x) return `<div class="half blank"><span class="no"></span><span class="tx"></span></div>`;
  const cls = x.type === "add" ? "add" : x.type === "del" ? "del" : "";
  return `<div class="half ${cls}"><span class="no">${(side === "l" ? x.old_no : x.new_no) ?? ""}</span><span class="tx">${esc(x.text)}</span></div>`;
}
const splitRows = (h) => (h.side_by_side || []).map((r) => `<div class="s">${half(r.left, "l")}${half(r.right, "r")}</div>`).join("");

const VERDICT = {
  safe_by_tests: ["safe", "Verified by tests", "Your tests passed before and after the change, with no regressions."],
  unsafe: ["unsafe", "Breaks existing behavior", "Tests that passed before now fail. Do not apply this patch."],
  untested: ["untested", "Passes static checks only", "No tests ran, so behavior is unproven. Review the diff carefully."],
  rejected: ["rejected", "Rejected", "The proposed patch failed its own safety checks."],
  success: ["safe", "Verified by tests", "A fix passed on the first try or after retries."],
  gave_up: ["gave_up", "No safe fix found", "Every attempt failed. The log below shows why."],
};
export function verdictOf(key) { return VERDICT[key] || ["untested", key, ""]; }

export function evidence(r) {
  // r = verify result or a loop attempt; build the checklist of what was actually checked.
  const rows = [];
  const rejected = r.verdict === "rejected";
  (r.fix_checks || []).forEach((c) => rows.push([rejected ? "fail" : "pass", c.replace(/\.$/, ""), ""]));
  (r.fix_warnings || []).forEach((w) => rows.push(["warn", w, ""]));
  const before = r.tests_before_passed || [], after = r.tests_after_passed || [], reg = r.regressions || {};
  const ran = before.length || after.length || Object.keys(reg).length;
  if (ran) {
    rows.push([before.length ? "pass" : "skip", `Tests before the patch: ${before.length} passing`, before.join(", ")]);
    Object.keys(reg).length
      ? Object.entries(reg).forEach(([t, why]) => rows.push(["fail", `Regression: ${t}`, why]))
      : rows.push(["pass", `Tests after the patch: ${after.length} passing, no regressions`, ""]);
  } else rows.push(["skip", "Behavior tests", "None ran. Add test code or enable auto-generated tests to prove the patch."]);
  (r.notes || []).forEach((n) => rows.push(["skip", n, ""]));
  const ic = { pass: "check", fail: "x", warn: "alert", skip: "minus" };
  return `<div class="evidence">${rows.map(([k, t, d]) => `<div class="ev ${k}"><span class="i">${icon(ic[k])}</span><div>${esc(t)}${d ? `<div class="d">${esc(d)}</div>` : ""}</div></div>`).join("")}</div>`;
}

export function verdictCard(key, r, extraTitle) {
  const [cls, title, sub] = verdictOf(key);
  const ic = cls === "safe" ? "check" : cls === "untested" ? "minus" : "x";
  return `<div class="verdict ${cls}"><div class="verdict-top"><span class="seal">${icon(ic)}</span><div><h3>${esc(extraTitle || title)}</h3><p>${esc(r.summary || sub)}</p></div></div>${r ? evidence(r) : ""}</div>`;
}
