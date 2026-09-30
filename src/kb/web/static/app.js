const SIGNAL_INFO = {
  country: "Country matches yours",
  department: "Department matches yours",
  location: "Your office / site",
  validity: "Still in force (valid from / until)",
  freshness: "Recently updated",
  source: "Official vs informal source",
  owner: "Has an accountable owner",
  uploader: "Uploaded by a group manager",
  conflicts: "Does not contradict a more trustworthy result",
};
const SIGNAL_TIPS = {
  country: "+30% for your country, −50% for another country.",
  department: "+20% for your department, −20% for another department.",
  location: "+10% when it is specific to your office or site.",
  validity: "−60% when expired, −30% when not yet valid.",
  freshness: "+20% when updated within a year, −30% when older than 3 years.",
  source: "+20% for official policy, −10% for Teams, email or chat.",
  owner: "−10% when nobody is accountable for the document.",
  uploader: "+10% when uploaded by a manager of the group it is shared with.",
  conflicts: "−40% when it states a different value than a more trustworthy result about the same thing (needs 'Show where results disagree').",
};
const PRESETS = {
  context: { name: "Relevance only vs Context-aware", note: "Same search, same documents. Right side re-ranks by what applies to you and what can be trusted.",
    a: { context_ranking: false }, b: { context_ranking: true } },
  hybrid: { name: "Keyword (BM25) vs Hybrid", note: "Hybrid adds meaning-based matches: try the Dutch query 'dubbel vakantiegeld'.",
    a: { mode: "bm25" }, b: { mode: "hybrid" } },
  rerank: { name: "Without vs with rerank", note: "Same search logic; right side lets the cross-encoder re-score the top 20. Context ranking is off on both sides so you see the rerank alone.",
    a: { rerank: false, context_ranking: false }, b: { rerank: true, context_ranking: false } },
  vector: { name: "Keyword vs Meaning", note: "Keyword needs shared words. Meaning finds paraphrases and other languages.",
    a: { mode: "bm25" }, b: { mode: "vector" } },
  filters: { name: "No filters vs Sidebar filters", note: "Right side applies the filters set in the sidebar (tick 'Apply filters').",
    a: { filters: false }, b: { filters: true } },
  trustonly: { name: "Context: only 'applies to me' vs only 'trust'", note: "Left uses country/department/site. Right uses validity/freshness/source/owner/uploader.",
    a: { signals: ["country", "department", "location"] }, b: { signals: ["validity", "freshness", "source", "owner", "uploader"] } },
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let me = null, mode = "bm25";

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

// ---------- login ----------
async function showLogin() {
  $("appView").classList.add("hidden"); $("whoami").classList.add("hidden"); $("loginView").classList.remove("hidden");
  const users = await api("/api/users");
  $("userList").innerHTML = users.map((u) => `
    <button class="user" data-email="${esc(u.email)}" data-admin="${u.is_admin ? "true" : "false"}" data-tip="Log in as ${esc(u.name)}. Results depend on this person's country, department and groups.">
      <b>${esc(u.name)}</b>
      <div class="hint">${esc(u.position || (u.is_admin ? "Administrator: sees everything" : ""))}</div>
      <div class="chips">${u.country ? `<span class="chip">${esc(u.country)}</span>` : ""}${u.location ? `<span class="chip">${esc(u.location)}</span>` : ""}${u.department ? `<span class="chip">${esc(u.department)}</span>` : ""}</div>
    </button>`).join("");
  document.querySelectorAll(".user").forEach((b) => b.onclick = async () => {
    let password = "";
    if (b.dataset.admin === "true") {
      password = prompt("Admin accounts see every document. Admin password:") || "";
      if (!password) return;
    }
    try { await api("/api/login", { email: b.dataset.email, password }); boot(); } catch (e) { alert(e.message); }
  });
}

async function boot() {
  try { me = await api("/api/me"); } catch { return showLogin(); }
  $("loginView").classList.add("hidden"); $("appView").classList.remove("hidden");
  $("whoami").classList.remove("hidden");
  $("whoami").innerHTML = `<span><b>${esc(me.name)}</b> <span class="hint">${esc([me.country, me.location, me.department].filter(Boolean).join(" · "))}</span></span>
    <button class="btn ghost small" id="switchBtn" data-tip="Log out and pick another demo user.">Switch user</button>`;
  $("switchBtn").onclick = async () => { await api("/api/logout", {}); showLogin(); };
  $("adminBox").classList.toggle("hidden", !me.is_admin);
  document.querySelector('#tabs button[data-tab="search"]').click();
  mode = me.vector_enabled ? "hybrid" : "bm25";
  renderMode();
  $("uGroups").innerHTML = me.groups.length ? me.groups.map((g) => `<label class="tog"><input type="checkbox" value="${esc(g.name)}" checked> ${esc(g.name)} <span class="hint">(${esc(g.role)})</span></label>`).join("") : '<span class="hint">You are in no groups: the document stays private.</span>';
  await loadDocs();
  loadNotes();
  runSearch();
}

// ---------- sidebar ----------
function renderMode() {
  document.querySelectorAll("#modeSeg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === mode);
    b.disabled = b.dataset.mode !== "bm25" && !me.vector_enabled;
  });
  $("modeHint").textContent = me.vector_enabled ? "" : "Vector search is off on this server (KB_VECTOR_ENABLED).";
  $("rerankOn").disabled = !me.rerank_enabled;
  if (!me.rerank_enabled) $("rerankOn").checked = false;
}
document.querySelectorAll("#modeSeg button").forEach((b) => b.onclick = () => { mode = b.dataset.mode; renderMode(); runSearch(); });
$("signalList").innerHTML = Object.entries(SIGNAL_INFO).map(([k, v]) => `<label class="tog" data-tip="${esc(SIGNAL_TIPS[k])}"><input type="checkbox" class="sig" value="${k}" checked><span>${esc(v)}<small>${k}</small></span></label>`).join("");
["rerankOn", "ctxOn", "filtersOn", "showConflicts", "recommendPeople", "showMeta", "showWhy", "showTrust", "topK", "fCountry", "fDepartment", "fSource", "fLanguage", "fTag", "fValidOn"].forEach((id) => $(id).addEventListener("change", () => { if (id === "ctxOn") syncSignals(); runSearch(); }));
document.addEventListener("change", (e) => { if (e.target.classList.contains("sig")) runSearch(); });
function syncSignals() { document.querySelectorAll(".sig").forEach((c) => c.disabled = !$("ctxOn").checked); }

function fillSelect(id, values) {
  const cur = $(id).value;
  $(id).innerHTML = '<option value="">any</option>' + [...values].sort().map((v) => `<option>${esc(v)}</option>`).join("");
  $(id).value = cur;
}

function filters() {
  if (!$("filtersOn").checked) return {};
  return { country: $("fCountry").value || null, department: $("fDepartment").value || null, source: $("fSource").value || null,
    language: $("fLanguage").value || null, tags: $("fTag").value ? [$("fTag").value] : [], valid_on: $("fValidOn").value || null };
}
function currentConfig() {
  return { mode, top_k: +$("topK").value || 8, context_ranking: $("ctxOn").checked, rerank: $("rerankOn").checked,
    detect_conflicts: $("showConflicts").checked, recommend_people: $("recommendPeople").checked,
    signals: [...document.querySelectorAll(".sig:checked")].map((c) => c.value), filters: filters() };
}

// ---------- search ----------
function pct(x) { return `${x >= 1 ? "+" : "−"}${Math.abs(Math.round((x - 1) * 100))}%`; }
function hitCard(h, i) {
  const showMeta = $("showMeta").checked, showWhy = $("showWhy").checked, showTrust = $("showTrust").checked;
  const factor = h.relevance ? h.score / h.relevance : 1;
  const why = [];
  if (h.bm25_rank) why.push(`<b>keyword #${h.bm25_rank}</b> (BM25 ${h.bm25_score.toFixed(2)}: ${h.matched_terms.map(esc).join(", ")})`);
  if (h.vector_rank) why.push(`<b>meaning #${h.vector_rank}</b> (cosine ${h.vector_score.toFixed(2)})`);
  if (h.rerank_rank) why.push(`<b>rerank #${h.rerank_rank}</b> (cross-encoder ${Math.round(h.rerank_score * 100)}% relevant)`);
  const meta = [
    ["country", h.country || "all"], ["site", h.location], ["dept", h.department], ["source", h.source], ["owner", h.owner || "none"],
    ["updated", h.updated_at ? h.updated_at.slice(0, 10) : "unknown"], ["valid", (h.valid_from || h.valid_until) ? `${h.valid_from || "…"} → ${h.valid_until || "…"}` : null],
    ["lang", h.language], ["tags", (h.tags || []).join(", ")], ["uploaded by", h.uploader_position], ["groups", h.groups.join(", ") || "private"],
  ].filter(([, v]) => v);
  return `<div class="panel hit">
    <div class="top"><span class="rank">${i + 1}</span><h4>${esc(h.title)}</h4>
      <div class="score">score ${h.score.toFixed(4)}${h.relevance && Math.abs(factor - 1) > 1e-9 ? `<br>relevance ${h.relevance.toFixed(4)} <span class="factor ${factor >= 1 ? "up" : "down"}">${pct(factor)}</span>` : ""}</div></div>
    ${showMeta ? `<div class="chips">${meta.map(([k, v]) => `<span class="chip"><span class="k">${k}</span> ${esc(v)}</span>`).join("")}</div>` : ""}
    ${showWhy ? `<div class="why">Matched: ${why.join(" · ") || "-"}</div>` : ""}
    ${showTrust && (h.reasons.length || h.warnings.length) ? `<div class="signals">
      ${h.reasons.length ? `<div class="box good"><div class="t">${h.warnings.length ? "What speaks for it" : "Why you can rely on it"}</div><ul>${h.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : "<div></div>"}
      ${h.warnings.length ? `<div class="box warn"><div class="t">Be careful</div><ul>${h.warnings.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
    </div>` : ""}
    ${conflictBox(h)}
    <div class="snippet">${esc(h.text.replace(/^SYNTHETIC.*\n+|^SYNTHETISCH.*\n+/m, ""))}</div>
  </div>`;
}
function conflictBox(h) {
  const ds = h.disagreements || [];
  if (!ds.length || !$("showConflicts").checked) return "";
  if (ds[0].most_trusted) {
    return `<div class="box good" style="margin-top:8px"><div class="t">Other results contradict this, but this is the most trustworthy source</div><ul>${ds.map((d) =>
      `<li>${esc(d.label)}: this says <b>${esc(d.value)}</b>, #${d.other_rank} ${esc(d.other_title)} says <b>${esc(d.other_value)}</b></li>`).join("")}</ul></div>`;
  }
  return `<div class="box bad"><div class="t">Contradicts a more trustworthy source</div><ul>${ds.map((d) =>
    `<li>${esc(d.label)}: this says <b>${esc(d.value)}</b>, but #${d.other_rank} ${esc(d.other_title)} says <b>${esc(d.other_value)}</b></li>`).join("")}</ul></div>`;
}
function markValue(sentence, value) {
  const i = sentence.indexOf(value);
  return i < 0 ? esc(sentence) : esc(sentence.slice(0, i)) + `<mark>${esc(value)}</mark>` + esc(sentence.slice(i + value.length));
}
function disputeBox(d, hits) {
  const byRank = new Map(hits.map((h, i) => [i + 1, h]));
  const ctx = $("ctxOn").checked;
  return `<div class="panel dispute">
    <h4>⚠ The results disagree on the ${esc(d.label)}${d.topic ? `: <i>${esc(d.topic)}</i>` : ""}${ctx ? ` · trust <b>${esc(d.claims[0].text)}</b>` : ""}</h4>
    <div class="hint">Same topic, same country, different value. ${ctx
      ? "All claims are about the same fact, so the most trustworthy source wins on country, validity, freshness, source, owner and uploader, not on how well it matches your words."
      : "Context ranking is off, so there are no trust signals: the claims are only ordered by text relevance. Turn it on to see which one to trust."}</div>
    ${d.claims.map((c, i) => {
      const h = byRank.get(c.rank) || { reasons: [], warnings: [] };
      const verdict = !ctx ? `result #${c.rank}`
        : i === 0 ? `✓ Most trustworthy${h.reasons.length ? ": " + esc(h.reasons.slice(0, 4).join(", ")) : ""}`
        : (h.warnings.length ? "⚠ " + esc(h.warnings.join(", ")) : "⚠ less trustworthy than #" + d.claims[0].rank);
      return `<div class="claim ${i === 0 ? "best" : ""}"><span class="rank">#${c.rank}</span><span class="v">${esc(c.text)}</span>
        <div><b>${esc(c.title)}</b><q>${markValue(c.sentence, c.text)}</q><div class="verdict">${verdict}</div></div></div>`;
    }).join("")}
  </div>`;
}
function escalationBox(e) {
  if (!e) return "";
  const ask = e.level === "ask";
  const people = e.experts.map((p, i) => `<div class="person"><span class="rank">${i + 1}</span><div class="who">
      <b>${esc(p.name)}</b> <span class="hint">${esc(p.position || "")}${p.country ? " · " + esc(p.country) : " · group-wide"}${p.location ? " · " + esc(p.location) : ""}</span><br>
      <a href="mailto:${esc(p.email)}">${esc(p.email)}</a> <span class="hint">· speaks ${esc(p.languages.join(", "))}</span>
      ${$("showWhy").checked ? `<div class="why">Matched: ${p.matched_terms.length ? `<b>expertise</b> (${p.matched_terms.map(esc).join(", ")})` : "owns a document in your results"}${p.rerank_score != null ? ` · <b>rerank</b> ${Math.round(p.rerank_score * 100)}% relevant` : ""}</div>` : ""}
      <div class="signals">
        ${p.reasons.length ? `<div class="box good"><div class="t">${p.warnings.length ? "What speaks for them" : "Why this person"}</div><ul>${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : "<div></div>"}
        ${p.warnings.length ? `<div class="box warn"><div class="t">Be careful</div><ul>${p.warnings.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
      </div></div></div>`).join("") || '<div class="hint">No matching person in the expert directory.</div>';
  return `<div class="panel escalate ${ask ? "ask" : ""}">
    <h4>${ask ? "🙋 No trustworthy answer in the documents: ask a person" : "🙋 Want to be sure? Ask the owner to confirm"}</h4>
    <ul>${e.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${people}
  </div>`;
}
let searchSeq = 0;
async function runSearch() {
  if (!me) return;
  const seq = ++searchSeq;
  const q = $("q").value.trim();
  if (!q) { $("results").innerHTML = ""; return; }
  try {
    const { hits, disputes, escalation } = await api("/api/search", { query: q, ...currentConfig() });
    if (seq !== searchSeq) return;
    $("results").innerHTML = (escalation && escalation.level === "ask" ? escalationBox(escalation) : "")
      + (hits.length ? disputes.map((d) => disputeBox(d, hits)).join("") : '<div class="note">No results you are allowed to see.</div>')
      + (escalation && escalation.level === "confirm" ? escalationBox(escalation) : "")
      + hits.map(hitCard).join("");
  } catch (e) { $("results").innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
}
$("searchBtn").onclick = runSearch;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });

// ---------- compare ----------
$("preset").innerHTML = Object.entries(PRESETS).map(([k, p]) => `<option value="${k}">${esc(p.name)}</option>`).join("");
function presetNote() { $("presetNote").textContent = PRESETS[$("preset").value].note; }
$("preset").onchange = () => { presetNote(); runCompare(); };
presetNote();
function configFor(over) {
  const base = currentConfig();
  const cfg = { ...base, ...over };
  if (over.filters === false) cfg.filters = {};
  if (over.filters === true) cfg.filters = base.filters;
  if (over.signals) cfg.context_ranking = true;
  if ((cfg.mode === "vector" || cfg.mode === "hybrid") && !me.vector_enabled) cfg.mode = "bm25";
  if (!me.rerank_enabled) cfg.rerank = false;
  return cfg;
}
function describe(cfg) {
  const names = { bm25: "Keyword", vector: "Meaning", hybrid: "Hybrid" };
  return `${names[cfg.mode]}${cfg.rerank ? " + rerank" : ""} · ${cfg.context_ranking ? "context on" : "relevance only"}${Object.values(cfg.filters).some((v) => v && v.length !== 0) ? " · filtered" : ""}`;
}
function compareRows(hits, other) {
  const pos = new Map(other.map((h, i) => [h.chunk_id, i]));
  return hits.map((h, i) => {
    let mv = '<span class="move new">new</span>';
    if (other !== hits && pos.has(h.chunk_id)) {
      const d = pos.get(h.chunk_id) - i;
      mv = d > 0 ? `<span class="move up">▲${d}</span>` : d < 0 ? `<span class="move down">▼${-d}</span>` : '<span class="move same">=</span>';
    }
    if (other === hits) mv = "";
    const warn = (h.warnings.length ? `<div class="hint" style="color:var(--warn)">⚠ ${h.warnings.map(esc).join("; ")}</div>` : "")
      + ((h.disagreements || []).length ? `<div class="hint" style="color:${h.disagreements[0].most_trusted ? "var(--good)" : "var(--bad)"}">${h.disagreements[0].most_trusted ? "✓ most trustworthy on" : "≠"} ${h.disagreements.map((d) => `${esc(d.value)} vs #${d.other_rank} ${esc(d.other_value)}`).join("; ")}</div>` : "");
    return `<div class="row"><span class="rank">${i + 1}</span><div class="t"><b>${esc(h.title)}</b>
      <div class="hint">${esc(h.country || "all countries")} · ${esc(h.source || "?")} · updated ${esc(h.updated_at ? h.updated_at.slice(0, 10) : "?")} · score ${h.score.toFixed(4)}</div>${warn}</div>${mv}</div>`;
  }).join("") || '<div class="hint">No results.</div>';
}
async function runCompare() {
  const p = PRESETS[$("preset").value];
  const q = $("cq").value.trim();
  if (!q) return;
  const a = configFor(p.a), b = configFor(p.b);
  $("colAName").textContent = "A: " + describe(a);
  $("colBName").textContent = "B: " + describe(b);
  try {
    const [ra, rb] = await Promise.all([api("/api/search", { query: q, ...a }), api("/api/search", { query: q, ...b })]);
    const ha = ra.hits, hb = rb.hits;
    $("colA").innerHTML = compareRows(ha, ha);
    $("colB").innerHTML = compareRows(hb, ha);
  } catch (e) { $("colA").innerHTML = `<div class="note err">${esc(e.message)}</div>`; $("colB").innerHTML = ""; }
}
$("compareBtn").onclick = runCompare;
$("cq").addEventListener("keydown", (e) => { if (e.key === "Enter") runCompare(); });

// ---------- documents ----------
async function loadDocs() {
  const { documents } = await api("/api/documents");
  $("docsNote").innerHTML = `You can read <b>${documents.length}</b> documents. Everything else is invisible to you: row-level security in PostgreSQL enforces this for every query, including search. Switch user to see the list change.`;
  $("docsTable").innerHTML = `<tr><th>Title</th><th>Country</th><th>Site</th><th>Dept</th><th>Source</th><th>Owner</th><th>Updated</th><th>Valid</th><th>Tags</th><th>Uploaded by</th><th>Groups</th></tr>` +
    documents.map((d) => `<tr><td>${esc(d.title)}</td><td>${esc(d.country || "all")}</td><td>${esc(d.location || "")}</td><td>${esc(d.department || "")}</td><td>${esc(d.source || "")}</td>
      <td>${esc(d.owner || "—")}</td><td>${esc(d.updated_at ? d.updated_at.slice(0, 10) : "")}</td><td>${esc((d.valid_from || d.valid_until) ? `${d.valid_from || "…"} → ${d.valid_until || "…"}` : "")}</td>
      <td>${esc((d.tags || []).join(", "))}</td><td>${esc(d.uploader_position || "")}${d.uploader_is_manager ? " (manager)" : ""}</td><td>${esc(d.groups.join(", ") || "private")}</td></tr>`).join("");
  const uniq = (f) => new Set(documents.flatMap(f).filter(Boolean));
  fillSelect("fCountry", uniq((d) => [d.country])); fillSelect("fDepartment", uniq((d) => [d.department]));
  fillSelect("fSource", uniq((d) => [d.source])); fillSelect("fLanguage", uniq((d) => [d.language])); fillSelect("fTag", uniq((d) => d.tags || []));
}

// ---------- notifications ----------
async function loadNotes() {
  const notes = await api("/api/notifications");
  const unread = notes.filter((n) => !n.read_at).length;
  $("noteBadge").textContent = unread; $("noteBadge").classList.toggle("hidden", !unread);
  $("notes").innerHTML = notes.length ? notes.map((n) => `<div class="panel notif ${n.read_at ? "" : "unread"}"><div class="m">${esc(n.message)}<div class="hint">${esc(n.created_at.slice(0, 16).replace("T", " "))}</div></div>
    ${n.read_at ? '<span class="hint">read</span>' : `<button class="btn ghost small" data-id="${n.id}" data-tip="Mark this notification as read.">Mark read</button>`}</div>`).join("") : '<div class="note">No notifications.</div>';
  document.querySelectorAll("#notes button[data-id]").forEach((b) => b.onclick = async () => { await api(`/api/notifications/${b.dataset.id}/read`, {}); loadNotes(); });
}

// ---------- upload ----------
$("uploadBtn").onclick = async () => {
  const val = (id) => $(id).value.trim() || null;
  const body = { title: $("uTitle").value.trim(), body: $("uBody").value, detect_duplicates: $("uDetect").checked,
    groups: [...document.querySelectorAll("#uGroups input:checked")].map((c) => c.value),
    source: val("uSource"), owner: val("uOwner"), country: val("uCountry"), department: val("uDepartment"), location: val("uLocation"),
    language: val("uLanguage"), tags: ($("uTags").value || "").split(",").map((t) => t.trim()).filter(Boolean),
    updated_at: val("uUpdated"), valid_from: val("uFrom"), valid_until: val("uUntil") };
  try {
    const r = await api("/api/upload", body);
    $("uploadResult").innerHTML = `<div class="note">Stored as document #${r.doc_id}.</div>` + (r.similar.length
      ? `<div class="box warn"><div class="t">Looks similar to existing knowledge</div><ul>${r.similar.map((s) => `<li>${esc(s.title || "a document you don't have access to")}: ${s.lexical_score != null ? `text overlap ${Math.round(s.lexical_score * 100)}%` : ""}${s.vector_score != null ? ` meaning ${Math.round(s.vector_score * 100)}%` : ""}</li>`).join("")}</ul>Managers and the other uploader were notified.</div>`
      : `<div class="hint">${body.detect_duplicates ? "No near-duplicates found." : "Duplicate check was off."}</div>`);
    await loadDocs(); loadNotes(); runSearch();
  } catch (e) { $("uploadResult").innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
};

let uploadExamples = null;
async function fillExample(prefix) {
  uploadExamples = uploadExamples || await api("/api/upload-examples");
  const ex = uploadExamples.find((e) => e.file.startsWith(prefix));
  if (!ex) return;
  const set = (id, v) => { $(id).value = v || ""; };
  set("uTitle", ex.title); set("uBody", ex.body); set("uSource", ex.source); set("uOwner", ex.owner); set("uCountry", ex.country);
  set("uDepartment", ex.department); set("uLocation", ex.location); set("uLanguage", ex.language); set("uTags", ex.tags);
  set("uUpdated", ex.updated_at); set("uFrom", ex.valid_from); set("uUntil", ex.valid_until);
  $("uploadResult").innerHTML = "";
}
$("exUnique").onclick = () => fillExample("unique");
$("exSimilar").onclick = () => fillExample("similar");

// ---------- tabs & admin ----------
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "compare") runCompare();
  if (b.dataset.tab === "notes") loadNotes();
  if (b.dataset.tab === "docs") loadDocs();
});
$("resetBtn").onclick = async () => {
  const password = prompt("Reset deletes all uploads and notifications and reloads the demo data.\nAdmin password:");
  if (!password) return;
  $("resetBtn").disabled = true;
  try { await api("/api/admin/reset", { password }); await api("/api/logout", {}); showLogin(); } catch (e) { alert(e.message); }
  $("resetBtn").disabled = false;
};

// ---------- hover info: only after the pointer rests 0.5 s on an element ----------
const tip = document.createElement("div");
tip.id = "tip";
document.body.appendChild(tip);
let tipTarget = null, tipTimer = null, tipShown = false;
function hideTip() { clearTimeout(tipTimer); tip.classList.remove("show"); tipShown = false; }
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-tip]");
  if (el !== tipTarget) { tipTarget = el; hideTip(); }
  if (!el || tipShown) return;
  clearTimeout(tipTimer);  // the pointer moved: restart the 0.5 s wait
  tipTimer = setTimeout(() => {
    tip.textContent = el.dataset.tip;
    const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
    const below = e.clientY + 18 + tip.offsetHeight < window.innerHeight;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${below ? e.clientY + 18 : e.clientY - tip.offsetHeight - 10}px`;
    tip.classList.add("show");
    tipShown = true;
  }, 500);
});
const resetTip = () => { tipTarget = null; hideTip(); };
["mousedown", "keydown", "scroll"].forEach((ev) => document.addEventListener(ev, resetTip, true));
document.documentElement.addEventListener("mouseleave", resetTip);  // pointer left the window

boot();
