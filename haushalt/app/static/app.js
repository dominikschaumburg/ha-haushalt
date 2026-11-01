import { esc, lineChart, heatmap, heatLegend, groupedColumns, splitBar, dayKey, tip, hideTip } from "./charts.js";

// ---------------------------------------------------------------- Zustand & API

const S = {
  me: null, persons: [], categories: [], tasks: [], reaction_set: [], settings: {}, ha: false, catalog: null, areas: [],
  view: new URLSearchParams(location.search).get("v") || localGet("view") || "tasks",
  search: "", room: localGet("room") || "all",
  history: { days: 30, person: "all", category: "all", deleted: false, rows: [] },
  stats: { range: localGet("range") || "90", metric: localGet("metric") || "time", rows: [], prev: [] },
};

function localGet(k) { try { return localStorage.getItem(`haushalt.${k}`); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(`haushalt.${k}`, v); } catch { /* egal */ } }

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`api/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function loadState() {
  Object.assign(S, await api("state"));
}

const $ = (sel, root = document) => root.querySelector(sel);
const person = (id) => S.persons.find((p) => p.id === id);
const task = (id) => S.tasks.find((t) => t.id === id);
const category = (id) => S.categories.find((c) => c.id === id);
const areaName = (id) => S.areas.find((a) => a.id === id)?.name;
const pcolor = (p) => `var(--series-${p?.color || 1})`;
const activePersons = () => S.persons.filter((p) => p.active);
const effort = (t) => t.effective_minutes ?? t.minutes;

// ---------------------------------------------------------------- Zeit & Format

const DAY = 86400000;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const daysBetween = (a, b) => Math.round((startOfDay(b) - startOfDay(a)) / DAY);
const monday = (d) => addDays(startOfDay(d), -((d.getDay() + 6) % 7));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function fmtMin(m) {
  m = Math.round(m);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
const fmtHours = (m) => `${(m / 60).toLocaleString("de-DE", { maximumFractionDigits: m < 600 ? 1 : 0 })} h`;
const fmtCount = (n) => n.toLocaleString("de-DE", { maximumFractionDigits: 1 });
const fmtShort = (m) => (m < 60 ? `${Math.round(m)} min` : fmtHours(m));

function relDay(date) {
  const d = daysBetween(date, new Date());
  if (d === 0) return "heute";
  if (d === 1) return "gestern";
  if (d < 7) return `vor ${d} Tagen`;
  if (d < 14) return "vor einer Woche";
  if (d < 60) return `vor ${Math.floor(d / 7)} Wochen`;
  return date.toLocaleDateString("de-DE", { day: "numeric", month: "short", year: d > 300 ? "numeric" : undefined });
}

function dayTitle(date) {
  const d = daysBetween(date, new Date());
  const long = date.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" });
  return d === 0 ? `Heute · ${long}` : d === 1 ? `Gestern · ${long}` : long;
}

const timeStr = (d) => d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

function localInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const INTERVALS = [
  [0, "Kein fester Rhythmus"], [1, "Täglich"], [2, "Alle 2 Tage"], [3, "Alle 3 Tage"], [4, "Alle 4 Tage"],
  [7, "Wöchentlich"], [14, "Alle 2 Wochen"], [21, "Alle 3 Wochen"], [30, "Monatlich"], [60, "Alle 2 Monate"],
  [90, "Vierteljährlich"], [182, "Halbjährlich"], [365, "Jährlich"],
];
const intervalName = (n) => INTERVALS.find(([d]) => d === n)?.[1] || `Alle ${n} Tage`;

const RANK = { overdue: 4, due: 3, soon: 2, ok: 1 };

/** Dringendere von Rhythmus und Sensor-Regel (die Sensor-Regel gilt auch vor dem ersten Abhaken). */
function due(t) {
  if (!t.active) return null;
  const iv = intervalDue(t);
  const sv = t.sensor_state && { ...t.sensor_state, diff: { due: 0, soon: 1 }[t.sensor_state.status] ?? 99 };
  const parts = [iv, sv].filter((d) => d && RANK[d.status]);
  if (!parts.length) return iv || sv || null;
  return parts.reduce((a, b) => (RANK[b.status] > RANK[a.status] || (RANK[b.status] === RANK[a.status] && b.frac > a.frac) ? b : a));
}

const interval = (t) => t.effective_interval ?? t.interval_days;

function intervalDue(t) {
  const n = interval(t);
  if (!n) return null;
  if (!t.last_done) return { status: "new", diff: Infinity, frac: 0, label: "Rhythmus startet beim ersten Abhaken" };
  const last = new Date(t.last_done);
  const elapsed = daysBetween(last, new Date());
  const diff = n - elapsed;
  const frac = Math.min(1, elapsed / n);
  if (diff < 0) return { status: "overdue", diff, frac, label: `Seit ${plural(-diff, "Tag", "Tagen")} überfällig` };
  if (diff === 0) return { status: "due", diff, frac, label: "Heute fällig" };
  const soon = diff <= Math.max(1, Math.round(n * 0.2));
  return { status: soon ? "soon" : "ok", diff, frac, label: diff === 1 ? "Morgen fällig" : `Fällig in ${diff} Tagen` };
}

// ---------------------------------------------------------------- Rahmen

const VIEWS = [["tasks", "Aufgaben"], ["history", "Verlauf"], ["stats", "Statistik"], ["settings", "Einstellungen"]];

function frame() {
  const me = S.me;
  document.body.innerHTML = `
    <header class="top">
      <div class="brand"><span class="logo">🏠</span><span>Haushalt</span></div>
      <nav class="tabs" role="tablist">${VIEWS.map(([id, label]) =>
        `<button role="tab" data-view="${id}" aria-selected="${S.view === id}">${label}</button>`).join("")}</nav>
      ${me ? `<div class="me" title="Angemeldet als ${esc(me.name)}">${avatar(me)}<span>${esc(me.name)}</span></div>` : ""}
    </header>
    <main id="view"></main>
    <div id="toasts"></div>
    <dialog id="sheet" class="sheet"></dialog>`;
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
}

function avatar(p, size = "") {
  if (!p) return "";
  return `<span class="avatar ${size}" style="--c:${pcolor(p)}" title="${esc(p.name)}">${esc(p.name.slice(0, 1).toUpperCase())}</span>`;
}

function go(view) {
  S.view = view;
  localSet("view", view);
  document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-selected", b.dataset.view === view));
  render();
}

async function render() {
  hideTip();
  const el = $("#view");
  if (S.view === "tasks") return renderTasks(el);
  if (S.view === "history") return renderHistory(el);
  if (S.view === "stats") return renderStats(el);
  return renderSettings(el);
}

function toast(text, action) {
  const box = document.createElement("div");
  box.className = "toast";
  box.innerHTML = `<span>${text}</span>${action ? `<button>${esc(action.label)}</button>` : ""}`;
  $("#toasts").appendChild(box);
  const close = () => { box.classList.add("out"); setTimeout(() => box.remove(), 250); };
  if (action) box.querySelector("button").addEventListener("click", async () => { close(); await action.run(); });
  setTimeout(close, action ? 8000 : 3500);
}

function fail(e) {
  toast(`⚠️ ${esc(e.message || e)}`);
}

// ---------------------------------------------------------------- Aufgaben

async function renderTasks(el) {
  const weekStart = monday(new Date());
  const recentStart = new Date(Date.now() - 3 * DAY);
  const rows = await api(`completions?since=${(recentStart < weekStart ? recentStart : weekStart).toISOString()}`);
  const week = rows.filter((c) => new Date(c.done_at) >= weekStart);
  const recent = rows.filter((c) => new Date(c.done_at) >= recentStart);
  const q = S.search.trim().toLowerCase();
  const usedAreas = S.areas.filter((a) => S.tasks.some((t) => t.active && t.area_id === a.id));
  if (S.room !== "all" && !usedAreas.some((a) => a.id === S.room)) S.room = "all";
  const active = S.tasks.filter((t) => t.active && (!q || t.name.toLowerCase().includes(q))
    && (S.room === "all" || t.area_id === S.room));
  const urgent = active.filter((t) => ["overdue", "due"].includes(due(t)?.status))
    .sort((a, b) => due(a).diff - due(b).diff);
  const soon = active.filter((t) => due(t)?.status === "soon").sort((a, b) => due(a).diff - due(b).diff);
  const shown = new Set([...urgent, ...soon].map((t) => t.id));
  // Häufig Erledigtes nach oben: Kategorien nach Summe, Aufgaben nach Anzahl (bei Gleichstand die eigene Reihenfolge)
  const byCount = (a, b) => b.count - a.count;
  const groups = S.categories.map((c) => {
    const all = active.filter((t) => t.category_id === c.id);
    return { c, total: all.reduce((n, t) => n + t.count, 0), items: all.filter((t) => !shown.has(t.id)).sort(byCount) };
  });
  const loose = active.filter((t) => !category(t.category_id));
  if (loose.length) {
    groups.push({ c: { name: "Sonstiges", icon: "✨" }, total: loose.reduce((n, t) => n + t.count, 0), items: loose.filter((t) => !shown.has(t.id)).sort(byCount) });
  }
  groups.sort((a, b) => b.total - a.total);

  el.innerHTML = `
    ${onboardingCard()}
    ${weekCard(week)}
    ${thanksCard(recent)}
    <div class="search"><input type="search" placeholder="Aufgabe suchen …" value="${esc(S.search)}" aria-label="Aufgabe suchen"></div>
    ${usedAreas.length ? `<div class="filters rooms">${chips("room", [{ value: "all", html: "Alle Räume" }, ...usedAreas.map((a) => ({ value: a.id, html: esc(a.name) }))], [S.room], false)}</div>` : ""}
    ${urgent.length ? section("Jetzt dran", urgent, "Sortiert nach Dringlichkeit") : ""}
    ${soon.length ? section("Demnächst", soon) : ""}
    ${!urgent.length && !soon.length && !q ? `<div class="empty-state">🎉 Alles erledigt, was einen Rhythmus hat.</div>` : ""}
    ${groups.filter((g) => g.items.length).map((g) => section(`${g.c.icon} ${g.c.name}`, g.items)).join("")}
    ${q && !active.length ? `<div class="empty-state">Keine Aufgabe gefunden.</div>` : ""}`;

  const input = $(".search input", el);
  input.addEventListener("input", () => {
    S.search = input.value;
    const pos = input.selectionStart;
    renderTasks(el).then(() => { const i = $(".search input", el); i.focus(); i.setSelectionRange(pos, pos); });
  });
  el.querySelectorAll("[data-thanks] [data-react]").forEach((b) => b.addEventListener("click", async () => {
    const row = b.closest("[data-thanks]");
    row.classList.add("sent");
    await react(+row.dataset.thanks, b.dataset.react);
    setTimeout(() => renderTasks(el), 300);
  }));
  $("[data-goto-history]", el)?.addEventListener("click", () => go("history"));
  $("[data-onb-settings]", el)?.addEventListener("click", () => go("settings"));
  $("[data-onb-templates]", el)?.addEventListener("click", () => openTemplates());
  $("[data-onb-done]", el)?.addEventListener("click", async () => {
    try { S.settings = await api("settings", { method: "PATCH", body: { onboarding_done: "1" } }); renderTasks(el); } catch (e) { fail(e); }
  });
  bindChips($(".rooms", el) || document.createElement("div"), () => { S.room = chipValues(el, "room")[0]; localSet("room", S.room); renderTasks(el); });
  el.querySelectorAll("[data-check]").forEach((b) => b.addEventListener("click", () => quickDone(+b.dataset.check, b)));
  el.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openLog({ taskId: +b.dataset.open })));
}

/** Checkliste beim ersten Öffnen: Personen, Benachrichtigungen, Vorlagen. */
function onboardingCard() {
  if (!S.ha || S.settings.onboarding_done === "1") return "";
  const people = activePersons();
  const linked = people.filter((p) => p.notify_service);
  const step = (ok, html) => `<li class="${ok ? "ok" : ""}"><span class="onb-mark">${ok ? "✓" : "•"}</span><span>${html}</span></li>`;
  return `<section class="card onboarding">
    <div class="card-head"><h2>Erste Schritte</h2><button class="btn ghost sm" data-onb-done>Fertig</button></div>
    <ol class="onb">
      ${step(people.length > 0, people.length ? `Personen aus Home Assistant: <b>${people.map((p) => esc(p.name)).join(", ")}</b>` : "Noch keine Personen – in Home Assistant Personen mit Benutzerkonto anlegen")}
      ${step(linked.length === people.length && people.length, `Benachrichtigungen: ${linked.length} von ${people.length} Personen mit Handy verknüpft (Companion-App) <button class="btn ghost sm" data-onb-settings>Einstellungen</button>`)}
      ${step(false, `Aufgaben passend zu euren Räumen und Geräten <button class="btn ghost sm" data-onb-templates>Vorlagen ansehen</button>`)}
    </ol>
  </section>`;
}

function weekCard(rows) {
  const per = shares(rows, "time");
  const parts = activePersons().map((p) => ({ name: p.name, color: pcolor(p), value: per.get(p.id) || 0 }));
  const total = parts.reduce((a, p) => a + p.value, 0);
  return `<section class="card week">
    <div class="week-head"><h2>Diese Woche</h2><span class="muted">${plural(rows.length, "Aufgabe", "Aufgaben")} · ${fmtMin(total)}</span></div>
    ${splitBar(parts, { height: 8 })}
    <div class="week-people">${parts.map((p, i) => `<div>${avatar(activePersons()[i], "sm")}<span>${esc(p.name)}</span><b>${fmtMin(p.value)}</b></div>`).join("")}</div>
    ${receivedLine(rows)}
  </section>`;
}

function receivedLine(rows) {
  if (!S.me) return "";
  const got = rows.filter((c) => c.person_ids.includes(S.me.id)).flatMap((c) => c.reactions);
  if (!got.length) return "";
  const from = [...new Set(got.map((r) => person(r.person_id)?.name).filter(Boolean))];
  return `<div class="received">${reactSummary(got)}<span>${got.length === 1 ? "Ein Danke" : `${got.length}× Danke`} diese Woche von ${esc(from.join(" & "))}</span></div>`;
}

/** Reaktionen gruppiert nach Emoji, z. B. „❤️ 2 🙏“. */
function reactSummary(list, { cid, mine = false } = {}) {
  const groups = new Map();
  list.forEach((r) => groups.set(r.emoji, [...(groups.get(r.emoji) || []), r.person_id]));
  return [...groups.entries()].map(([emoji, ids]) => {
    const names = ids.map((id) => person(id)?.name).join(", ");
    const pressed = S.me && ids.includes(S.me.id);
    const attrs = cid && !mine ? `data-react-toggle="${emoji}" aria-pressed="${pressed}"` : "disabled";
    return `<button type="button" class="react-chip" ${attrs} title="${esc(names)}">${emoji}${ids.length > 1 ? `<span>${ids.length}</span>` : ""}</button>`;
  }).join("");
}

function thanksCard(rows) {
  if (!S.me || !S.reaction_set.length) return "";
  const all = rows.filter((c) => !c.person_ids.includes(S.me.id) && !c.reactions.some((r) => r.person_id === S.me.id));
  const open = all.slice(0, 3);
  if (!open.length) return "";
  return `<section class="card thanks"><div class="card-head"><h2>Danke sagen</h2><span class="muted">Das haben die anderen erledigt</span></div>
    ${open.map((c) => {
      const t = task(c.task_id) || {};
      const d = new Date(c.done_at);
      return `<div class="thanks-row" data-thanks="${c.id}">
        <span class="avatars">${c.person_ids.map((id) => avatar(person(id), "sm")).join("")}</span>
        <span class="thanks-text"><b>${esc(c.person_ids.map((id) => person(id)?.name).join(" & "))}</b> ${esc(t.icon)} ${esc(t.name)}<span class="muted"> · ${relDay(d)}, ${timeStr(d)}</span></span>
        <span class="react-row">${S.reaction_set.map((e) => `<button class="react-pick" data-react="${e}" aria-label="Mit ${e} danken">${e}</button>`).join("")}</span>
      </div>`;
    }).join("")}
    ${all.length > open.length ? `<button class="btn ghost sm more-thanks" data-goto-history>${all.length - open.length} weitere im Verlauf</button>` : ""}
  </section>`;
}

async function react(cid, emoji) {
  try {
    const c = await api(`completions/${cid}/reactions`, { method: "POST", body: { emoji } });
    const mine = c.reactions.some((r) => r.person_id === S.me.id && r.emoji === emoji);
    if (mine) toast(`${emoji} an ${esc(c.person_ids.map((id) => person(id)?.name).join(" & "))} geschickt`);
    return c;
  } catch (e) { fail(e); }
}

let pickerEl;
function openPicker(btn, onPick) {
  closePicker();
  pickerEl = document.createElement("div");
  pickerEl.className = "picker";
  pickerEl.innerHTML = S.reaction_set.map((e) => `<button class="react-pick" data-pick="${e}">${e}</button>`).join("");
  document.body.appendChild(pickerEl);
  const r = btn.getBoundingClientRect();
  const w = pickerEl.offsetWidth;
  pickerEl.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w))}px`;
  pickerEl.style.top = `${r.bottom + 6 + pickerEl.offsetHeight > window.innerHeight ? r.top - pickerEl.offsetHeight - 6 : r.bottom + 6}px`;
  pickerEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pick]");
    if (b) { closePicker(); onPick(b.dataset.pick); }
  });
  setTimeout(() => document.addEventListener("click", closePicker, { once: true }), 0);
}
function closePicker() {
  pickerEl?.remove();
  pickerEl = null;
}

function section(title, items, hint = "") {
  return `<section class="group"><h3>${esc(title)}${hint ? ` <span class="muted">${hint}</span>` : ""}</h3>
    <div class="task-list">${items.map(taskCard).join("")}</div></section>`;
}

function taskCard(t) {
  const d = due(t);
  const who = t.last_persons.map(person).filter(Boolean);
  const last = t.last_done ? `${who.map((p) => esc(p.name)).join(" & ")} ${relDay(new Date(t.last_done))}` : "";
  const status = d ? `<span class="status ${d.status}"><i></i>${d.label}</span>` : "";
  const room = S.room === "all" && areaName(t.area_id) ? `📍 ${esc(areaName(t.area_id))}` : "";
  const meta = [status, `≈ ${fmtMin(effort(t))}`, last, room].filter(Boolean).join('<span class="dot">·</span>');
  return `<div class="task ${d?.status || ""}">
    <button class="task-main" data-open="${t.id}">
      <span class="ticon" style="--frac:${d ? d.frac : 0}">${esc(t.icon || "•")}</span>
      <span class="tbody"><span class="tname">${esc(t.name)}</span><span class="tmeta">${meta}</span></span>
    </button>
    <button class="check" data-check="${t.id}" aria-label="${esc(t.name)} erledigt" title="Als erledigt markieren">
      <svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>
    </button>
  </div>`;
}

async function quickDone(taskId, btn) {
  if (!S.me) return openLog({ taskId });
  btn.classList.add("done");
  try {
    const c = await api("completions", { method: "POST", body: { task_id: taskId, person_ids: [S.me.id] } });
    await afterLogged(c);
  } catch (e) {
    btn.classList.remove("done");
    fail(e);
  }
}

async function afterLogged(c) {
  const t = task(c.task_id);
  const names = c.person_ids.map((id) => person(id)?.name).join(" & ");
  toast(`<b>${esc(t.name)}</b> erledigt · ${esc(names)}`, {
    label: "Rückgängig",
    run: async () => {
      await api(`completions/${c.id}`, { method: "DELETE" });
      await loadState();
      render();
      toast("Eintrag zurückgenommen");
    },
  });
  await loadState();
  setTimeout(render, 350);
}

// ---------------------------------------------------------------- Sheet: erfassen / bearbeiten

function openSheet(html, bind) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<form method="dialog" class="sheet-body">${html}</form>`;
  dlg.showModal();
  dlg.onclick = (e) => { if (e.target === dlg) dlg.close(); };
  dlg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); dlg.close(); }));
  bind(dlg);
}

function chips(name, options, selected, multi = true) {
  return `<div class="chips" data-chips="${name}" data-multi="${multi}">${options.map((o) =>
    `<button type="button" class="chip" data-value="${o.value}" aria-pressed="${selected.includes(o.value)}">${o.html}</button>`).join("")}</div>`;
}

function bindChips(root, onChange = () => {}) {
  root.querySelectorAll("[data-chips]").forEach((group) => {
    group.addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      if (group.dataset.multi === "true") {
        b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
      } else {
        group.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c === b));
      }
      onChange(group.dataset.chips);
    });
  });
}
const chipValues = (root, name) => [...root.querySelectorAll(`[data-chips="${name}"] .chip[aria-pressed="true"]`)].map((b) => b.dataset.value);

async function openLog({ taskId, entry }) {
  const t = task(entry ? entry.task_id : taskId);
  const people = activePersons().concat(entry ? entry.person_ids.map(person).filter((p) => p && !p.active) : []);
  const selected = entry ? entry.person_ids.map(String) : S.me ? [String(S.me.id)] : [];
  const when = entry ? new Date(entry.done_at) : new Date();
  const recent = entry ? [] : await api(`completions?task=${t.id}&limit=5`);
  const d = due(t);
  const taskOptions = S.tasks.filter((x) => x.active || x.id === t.id)
    .map((x) => `<option value="${x.id}" ${x.id === t.id ? "selected" : ""}>${esc(x.icon)} ${esc(x.name)}</option>`).join("");

  openSheet(`
    <div class="sheet-head">
      <span class="ticon big">${esc(t.icon || "•")}</span>
      <div><h2>${esc(t.name)}</h2><div class="muted">${[d?.label, interval(t) ? `${learnedRhythm(t) ? "⌀ " : ""}${intervalName(interval(t))}` : "Ohne festen Rhythmus", `≈ ${fmtMin(effort(t))}`].filter(Boolean).join(" · ")}</div></div>
      <button class="icon-btn" data-close aria-label="Schließen">✕</button>
    </div>
    ${entry ? `<label class="field"><span>Aufgabe</span><select name="task">${taskOptions}</select></label>` : ""}
    <div class="field"><span>Wer?</span>${chips("who", people.map((p) => ({ value: String(p.id), html: `${avatar(p, "sm")}${esc(p.name)}` })), selected)}</div>
    <div class="field"><span>Wann?</span>
      ${entry ? "" : chips("when", [{ value: "now", html: "Jetzt" }, { value: "1h", html: "Vor 1 Std." }, { value: "3h", html: "Vor 3 Std." }, { value: "yesterday", html: "Gestern" }], ["now"], false)}
      <input type="datetime-local" name="when" value="${localInput(when)}" max="${localInput(new Date(Date.now() + 3600000))}">
    </div>
    <label class="field"><span>Aufwand <em class="muted" data-share></em></span>
      <div class="stepper"><button type="button" data-step="-5">−</button><input type="number" name="minutes" min="0" step="1" inputmode="numeric" value="${Math.round(entry ? entry.minutes : effort(t))}"><span>min</span><button type="button" data-step="5">+</button></div>
    </label>
    <label class="field"><span>Notiz</span><input type="text" name="note" value="${esc(entry?.note || "")}" placeholder="optional"></label>
    <div class="sheet-actions">
      ${entry ? (entry.deleted_at
        ? `<button type="button" class="btn ghost" data-restore>Wiederherstellen</button>`
        : `<button type="button" class="btn danger" data-delete>Löschen</button>`) : ""}
      <button type="submit" class="btn primary" data-save>${entry ? "Speichern" : "Erledigt ✓"}</button>
    </div>
    ${recent.length ? `<div class="recent"><h4>Zuletzt</h4>${recent.map((c) =>
      `<div class="recent-row"><span>${relDay(new Date(c.done_at))}, ${timeStr(new Date(c.done_at))}</span><span>${c.person_ids.map((id) => avatar(person(id), "sm")).join("")}</span><span class="muted">${fmtMin(c.minutes)}</span></div>`).join("")}</div>` : ""}
  `, (dlg) => {
    const form = $("form", dlg);
    const updateShare = () => {
      const n = chipValues(dlg, "who").length;
      const m = +form.minutes.value || 0;
      $("[data-share]", dlg).textContent = n > 1 ? `· je ${fmtMin(m / n)}` : "";
    };
    bindChips(dlg, (name) => {
      if (name === "when") {
        const v = chipValues(dlg, "when")[0];
        const now = new Date();
        const at = v === "1h" ? new Date(now - 3600000) : v === "3h" ? new Date(now - 3 * 3600000)
          : v === "yesterday" ? new Date(addDays(now, -1).setHours(19, 0, 0, 0)) : now;
        form.when.value = localInput(at);
      }
      updateShare();
    });
    form.when.addEventListener("input", () => dlg.querySelectorAll('[data-chips="when"] .chip').forEach((c) => c.setAttribute("aria-pressed", false)));
    dlg.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
      form.minutes.value = Math.max(0, (+form.minutes.value || 0) + +b.dataset.step);
      updateShare();
    }));
    form.minutes.addEventListener("input", updateShare);
    updateShare();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {
        person_ids: chipValues(dlg, "who").map(Number),
        done_at: new Date(form.when.value).toISOString(),
        minutes: +form.minutes.value || 0,
        note: form.note.value,
      };
      if (!body.person_ids.length) return fail(new Error("Bitte mindestens eine Person auswählen"));
      try {
        if (entry) {
          body.task_id = +form.task.value;
          await api(`completions/${entry.id}`, { method: "PATCH", body });
          dlg.close();
          toast("Eintrag gespeichert");
          await loadState();
          render();
        } else {
          const c = await api("completions", { method: "POST", body: { ...body, task_id: t.id } });
          dlg.close();
          await afterLogged(c);
        }
      } catch (err) { fail(err); }
    });
    $("[data-delete]", dlg)?.addEventListener("click", async () => {
      await removeEntry(entry);
      dlg.close();
    });
    $("[data-restore]", dlg)?.addEventListener("click", async () => {
      await api(`completions/${entry.id}/restore`, { method: "POST" });
      dlg.close();
      toast("Eintrag wiederhergestellt");
      await loadState();
      render();
    });
  });
}

async function removeEntry(entry) {
  try {
    await api(`completions/${entry.id}`, { method: "DELETE" });
    toast(`<b>${esc(task(entry.task_id)?.name)}</b> gelöscht`, {
      label: "Rückgängig",
      run: async () => { await api(`completions/${entry.id}/restore`, { method: "POST" }); await loadState(); render(); },
    });
    await loadState();
    render();
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- Verlauf

async function renderHistory(el) {
  const H = S.history;
  const since = addDays(new Date(), -H.days);
  H.rows = await api(`completions?since=${startOfDay(since).toISOString()}${H.deleted ? "&deleted=1" : ""}`);
  const rows = H.rows.filter((c) => (H.person === "all" || c.person_ids.includes(+H.person))
    && (H.category === "all" || String(task(c.task_id)?.category_id) === H.category));
  const byDay = new Map();
  rows.forEach((c) => {
    const k = dayKey(new Date(c.done_at));
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(c);
  });

  el.innerHTML = `
    <div class="filters">
      ${chips("hp", [{ value: "all", html: "Alle" }, ...activePersons().map((p) => ({ value: String(p.id), html: `${avatar(p, "sm")}${esc(p.name)}` }))], [H.person], false)}
      <select data-hc aria-label="Kategorie"><option value="all">Alle Kategorien</option>${S.categories.map((c) =>
        `<option value="${c.id}" ${H.category === String(c.id) ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)}</option>`).join("")}</select>
      <label class="toggle"><input type="checkbox" data-hd ${H.deleted ? "checked" : ""}> Gelöschte zeigen</label>
    </div>
    ${byDay.size ? [...byDay.entries()].map(([k, list]) => {
      const live = list.filter((c) => !c.deleted_at);
      return `<section class="day"><h3>${dayTitle(new Date(k + "T12:00"))}<span class="muted">${plural(live.length, "Aufgabe", "Aufgaben")} · ${fmtMin(live.reduce((a, c) => a + c.minutes, 0))}</span></h3>
      <div class="card list">${list.map(entryRow).join("")}</div></section>`;
    }).join("") : `<div class="empty-state">In diesem Zeitraum wurde nichts erfasst.</div>`}
    <button class="btn ghost more" data-more>Ältere Einträge laden</button>`;

  bindChips(el, () => { H.person = chipValues(el, "hp")[0]; renderHistory(el); });
  $("[data-hc]", el).addEventListener("change", (e) => { H.category = e.target.value; renderHistory(el); });
  $("[data-hd]", el).addEventListener("change", (e) => { H.deleted = e.target.checked; renderHistory(el); });
  $("[data-more]", el).addEventListener("click", () => { H.days += 60; renderHistory(el); });
  el.querySelectorAll("[data-entry]").forEach((r) => r.addEventListener("click", (e) => {
    const c = H.rows.find((x) => x.id === +r.dataset.entry);
    const toggle = e.target.closest("[data-react-toggle]");
    if (toggle) return react(c.id, toggle.dataset.reactToggle).then(() => renderHistory(el));
    if (e.target.closest("[data-react-open]")) {
      e.stopPropagation();
      return openPicker(e.target.closest("[data-react-open]"), (emoji) => react(c.id, emoji).then(() => renderHistory(el)));
    }
    if (e.target.closest("[data-del]")) return removeEntry(c);
    if (e.target.closest("[data-undel]")) {
      return api(`completions/${c.id}/restore`, { method: "POST" }).then(loadState).then(render).then(() => toast("Eintrag wiederhergestellt"));
    }
    openLog({ entry: c });
  }));
}

function entryRow(c) {
  const t = task(c.task_id) || { name: "Gelöschte Aufgabe", icon: "?" };
  const d = new Date(c.done_at);
  const mine = !S.me || c.person_ids.includes(S.me.id);
  return `<div class="entry ${c.deleted_at ? "deleted" : ""}" data-entry="${c.id}" role="button" tabindex="0">
    <span class="time">${timeStr(d)}</span>
    <span class="ticon sm">${esc(t.icon)}</span>
    <span class="ename"><span class="ename-t">${esc(t.name)}${c.note ? `<span class="muted"> · ${esc(c.note)}</span>` : ""}</span>${reactSummary(c.reactions, { cid: c.id, mine })}</span>
    <span class="avatars">${c.person_ids.map((id) => avatar(person(id), "sm")).join("")}</span>
    <span class="emin">${fmtMin(c.minutes)}</span>
    <span class="actions">${!mine && !c.deleted_at && S.me ? `<button class="icon-btn react-add" data-react-open title="Danke sagen" aria-label="Danke sagen">♡</button>` : ""}${c.deleted_at
      ? `<button class="icon-btn" data-undel title="Wiederherstellen" aria-label="Wiederherstellen">↺</button>`
      : `<button class="icon-btn" data-del title="Löschen" aria-label="Löschen">🗑</button>`}</span>
  </div>`;
}

// ---------------------------------------------------------------- Statistik

const RANGES = [["28", "4 Wochen"], ["90", "3 Monate"], ["365", "12 Monate"], ["all", "Gesamt"]];

/** Anteile je Person: gemeinsame Aufgaben werden gleichmäßig geteilt. */
function shares(rows, metric) {
  const out = new Map();
  for (const c of rows) {
    const n = c.person_ids.length || 1;
    const v = metric === "time" ? c.minutes / n : 1 / n;
    c.person_ids.forEach((id) => out.set(id, (out.get(id) || 0) + v));
  }
  return out;
}

async function renderStats(el) {
  const st = S.stats;
  const today = startOfDay(new Date());
  const all = st.range === "all";
  let start = all ? null : addDays(today, -(+st.range) + 1);
  const rows = await api(`completions${start ? `?since=${start.toISOString()}` : ""}`);
  if (!start) start = rows.length ? startOfDay(new Date(rows[rows.length - 1].done_at)) : addDays(today, -27);
  const span = daysBetween(start, today) + 1;
  const prevStart = addDays(start, -span);
  const prev = all ? [] : await api(`completions?since=${prevStart.toISOString()}&until=${start.toISOString()}`);
  const metric = st.metric;
  const fmt = metric === "time" ? fmtMin : (v) => fmtCount(v);
  const people = activePersons();
  const cur = shares(rows, metric);
  const old = shares(prev, metric);
  const total = [...cur.values()].reduce((a, b) => a + b, 0);

  el.innerHTML = `
    <div class="filters">
      ${chips("range", RANGES.map(([v, l]) => ({ value: v, html: l })), [st.range], false)}
      ${chips("metric", [{ value: "time", html: "⏱ Zeit" }, { value: "count", html: "✓ Anzahl" }], [metric], false)}
    </div>
    ${rows.length ? "" : `<div class="empty-state">Noch keine Einträge in diesem Zeitraum – sobald ihr Aufgaben abhakt, erscheinen hier die Auswertungen.</div>`}
    <section class="tiles">${people.map((p) => {
      const v = cur.get(p.id) || 0;
      const o = old.get(p.id) || 0;
      const delta = all || !o ? "" : `<span class="delta">${v >= o ? "▲" : "▼"} ${Math.abs(Math.round(((v - o) / o) * 100))} % ggü. vorher</span>`;
      const count = shares(rows, "count").get(p.id) || 0;
      return `<div class="card tile">
        <div class="tile-head">${avatar(p)}<span>${esc(p.name)}</span><span class="share">${total ? Math.round((100 * v) / total) : 0} %</span></div>
        <div class="tile-value">${metric === "time" ? fmtHours(v) : fmtCount(v)}</div>
        <div class="muted">${metric === "time" ? `${Math.round(count)} Aufgaben` : fmtHours(shares(rows, "time").get(p.id) || 0)} · Ø ${fmt(v / Math.max(1, span / 7))} pro Woche</div>
        ${delta}
        ${(() => { const n = rows.filter((c) => c.person_ids.includes(p.id)).reduce((a, c) => a + c.reactions.length, 0);
          return n ? `<div class="thanks-count">❤️ ${n}× Danke bekommen</div>` : ""; })()}
      </div>`;
    }).join("")}</section>
    <section class="card">
      <h2>Aufteilung</h2>
      ${splitBar(people.map((p) => ({ name: p.name, color: pcolor(p), value: cur.get(p.id) || 0, tip: `${p.name}: ${fmt(cur.get(p.id) || 0)}` })), { height: 28, labels: true })}
      <div class="legend">${people.map((p) => `<span><i style="background:${pcolor(p)}"></i>${esc(p.name)}</span>`).join("")}</div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Verlauf</h2><span class="muted" data-bucket></span></div>
      <div class="legend">${people.map((p) => `<span><i class="line" style="background:${pcolor(p)}"></i>${esc(p.name)}</span>`).join("")}</div>
      <div data-trend></div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Aktivität</h2><span class="muted" data-heat-span></span></div>
      ${people.map((p) => `<div class="heat-row"><div class="heat-name">${avatar(p, "sm")}${esc(p.name)}${heatLegend(pcolor(p))}</div><div data-heat="${p.id}"></div></div>`).join("")}
    </section>
    <section class="card">
      <h2>Wer macht was?</h2>
      <div class="legend">${people.map((p) => `<span><i style="background:${pcolor(p)}"></i>${esc(p.name)}</span>`).join("")}</div>
      ${whoDoesWhat(rows, people, metric)}
    </section>
    ${roomsCard(rows, people, metric)}
    <section class="card">
      <div class="card-head"><h2>Wochentage</h2><span class="muted">Ø pro Wochentag</span></div>
      <div data-weekday></div>
    </section>`;

  bindChips(el, (name) => {
    st[name] = chipValues(el, name)[0];
    localSet(name, st[name]);
    renderStats(el);
  });
  el.querySelectorAll(".seg[data-tip]").forEach((s) => {
    const show = (e) => tip(e.clientX, s.getBoundingClientRect().top, esc(s.dataset.tip));
    s.addEventListener("pointerenter", show);
    s.addEventListener("pointerdown", show);
    s.addEventListener("pointerleave", hideTip);
  });

  if (!people.length) return;
  drawTrend(el, rows, people, start, today, metric, fmt);
  drawHeatmaps(el, rows, people, start, today);
  drawWeekdays(el, rows, people, start, today, metric, fmt);
}

function drawTrend(el, rows, people, start, today, metric, fmt) {
  const span = daysBetween(start, today) + 1;
  const monthly = span > 400;
  const buckets = [];
  if (monthly) {
    for (let d = new Date(start.getFullYear(), start.getMonth(), 1); d <= today; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
      buckets.push({ from: d, to: new Date(d.getFullYear(), d.getMonth() + 1, 1), label: d.toLocaleDateString("de-DE", { month: "short", year: "2-digit" }), tip: d.toLocaleDateString("de-DE", { month: "long", year: "numeric" }) });
    }
  } else {
    for (let d = monday(start); d <= today; d = addDays(d, 7)) {
      const kw = isoWeek(d);
      buckets.push({ from: d, to: addDays(d, 7), label: `KW ${kw}`, tip: `KW ${kw} · ${d.toLocaleDateString("de-DE", { day: "numeric", month: "short" })} – ${addDays(d, 6).toLocaleDateString("de-DE", { day: "numeric", month: "short" })}` });
    }
  }
  $("[data-bucket]", el).textContent = monthly ? "pro Monat" : "pro Woche";
  const series = people.map((p) => ({
    name: p.name, color: pcolor(p),
    values: buckets.map((b) => {
      const inB = rows.filter((c) => { const t = new Date(c.done_at); return t >= b.from && t < b.to; });
      return Math.round((shares(inB, metric).get(p.id) || 0) * 10) / 10;
    }),
  }));
  lineChart($("[data-trend]", el), { labels: buckets.map((b) => b.label), tipLabels: buckets.map((b) => b.tip), series, metric, fmt });
}

function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / DAY + 1) / 7);
}

function drawHeatmaps(el, rows, people, start, today) {
  // So viele Wochen, wie bei ~15px Kästchen in die Breite passen (mindestens der gewählte Zeitraum, höchstens ein Jahr)
  const fit = Math.floor(((el.querySelector("[data-heat]")?.clientWidth || 600) - 22) / 15);
  const weeks = Math.min(53, Math.max(fit, Math.ceil((daysBetween(start, today) + 1) / 7) + 1));
  const first = addDays(monday(today), -(weeks - 1) * 7);
  $("[data-heat-span]", el).textContent = `letzte ${weeks} Wochen`;
  for (const p of people) {
    const days = new Map();
    for (const c of rows) {
      if (!c.person_ids.includes(p.id)) continue;
      const k = dayKey(new Date(c.done_at));
      const info = days.get(k) || { minutes: 0, tasks: [] };
      info.minutes += c.minutes / c.person_ids.length;
      info.tasks.push(task(c.task_id)?.name || "?");
      days.set(k, info);
    }
    heatmap($(`[data-heat="${p.id}"]`, el), {
      start: first, weeks, days, color: pcolor(p), today,
      tipFor: (date, info) => `<div class="tip-title">${date.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "long" })}</div>`
        + (info ? `<div class="tip-row"><i style="background:${pcolor(p)}"></i>${esc(p.name)}<b>${fmtMin(info.minutes)}</b></div><div class="tip-list">${info.tasks.map(esc).join("<br>")}</div>`
          : `<div class="muted">nichts erfasst</div>`),
    });
  }
}

function drawWeekdays(el, rows, people, start, today, metric, fmt) {
  const occurrences = Array(7).fill(0);
  for (let d = start; d <= today; d = addDays(d, 1)) occurrences[(d.getDay() + 6) % 7]++;
  const names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const full = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
  const series = people.map((p) => {
    const vals = Array(7).fill(0);
    for (const c of rows) {
      if (!c.person_ids.includes(p.id)) continue;
      const wd = (new Date(c.done_at).getDay() + 6) % 7;
      vals[wd] += metric === "time" ? c.minutes / c.person_ids.length : 1 / c.person_ids.length;
    }
    return { name: p.name, color: pcolor(p), values: vals.map((v, i) => Math.round((v / Math.max(1, occurrences[i])) * 10) / 10) };
  });
  groupedColumns($("[data-weekday]", el), { groups: names, series, metric, fmt, tipTitle: (i) => `Ø ${full[i]}` });
}

function roomsCard(rows, people, metric) {
  const fmt = metric === "time" ? fmtShort : (v) => fmtCount(Math.round(v * 10) / 10);
  const list = S.areas.map((a) => {
    const sh = shares(rows.filter((r) => task(r.task_id)?.area_id === a.id), metric);
    return { a, sh, total: [...sh.values()].reduce((x, y) => x + y, 0) };
  }).filter((x) => x.total > 0).sort((x, y) => y.total - x.total);
  if (!list.length) return "";
  const max = list[0].total;
  return `<section class="card"><div class="card-head"><h2>Räume</h2><span class="muted">Aufgaben mit Raum</span></div>
    <div class="wdw">${list.map((x) => `<div class="wdw-task room-row"><span class="wdw-name">📍 ${esc(x.a.name)}</span><span class="wdw-total">${fmt(x.total)}</span>
      <div style="width:${Math.max(4, (100 * x.total) / max)}%">${splitBar(people.map((p) => ({ name: p.name, color: pcolor(p), value: x.sh.get(p.id) || 0, tip: `${p.name}: ${fmt(x.sh.get(p.id) || 0)}` })), { height: 14 })}</div></div>`).join("")}</div>
  </section>`;
}

function whoDoesWhat(rows, people, metric) {
  const fmt = metric === "time" ? fmtShort : (v) => fmtCount(Math.round(v * 10) / 10);
  const cats = [...S.categories, { id: null, name: "Sonstiges", icon: "✨" }].map((c) => {
    const inCat = rows.filter((r) => (task(r.task_id)?.category_id ?? null) === c.id || (c.id === null && !category(task(r.task_id)?.category_id)));
    const tasksIn = [...new Set(inCat.map((r) => r.task_id))].map((id) => {
      const list = inCat.filter((r) => r.task_id === id);
      const sh = shares(list, metric);
      return { t: task(id), sh, total: [...sh.values()].reduce((a, b) => a + b, 0) };
    }).sort((a, b) => b.total - a.total);
    const sh = shares(inCat, metric);
    return { c, sh, total: [...sh.values()].reduce((a, b) => a + b, 0), tasksIn };
  }).filter((x) => x.total > 0).sort((a, b) => b.total - a.total);
  if (!cats.length) return `<div class="muted">Noch keine Daten.</div>`;
  const bar = (sh, h) => splitBar(people.map((p) => ({ name: p.name, color: pcolor(p), value: sh.get(p.id) || 0, tip: `${p.name}: ${fmt(sh.get(p.id) || 0)}` })), { height: h, labels: h >= 20 });
  return `<div class="wdw">${cats.map((x) => `<details class="wdw-cat">
      <summary><span class="wdw-name">${esc(x.c.icon)} ${esc(x.c.name)}</span><span class="wdw-total">${fmt(x.total)}</span>${bar(x.sh, 22)}</summary>
      ${x.tasksIn.map((y) => `<div class="wdw-task"><span class="wdw-name">${esc(y.t?.icon)} ${esc(y.t?.name)}</span><span class="wdw-total">${fmt(y.total)}</span>${bar(y.sh, 10)}</div>`).join("")}
    </details>`).join("")}</div>`;
}

// ---------------------------------------------------------------- Einstellungen

function renderSettings(el) {
  const archived = S.tasks.filter((t) => !t.active);
  el.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>Personen</h2><button class="btn ghost sm" data-add-person>+ Person</button></div>
      <p class="muted">Personen mit Home-Assistant-Konto werden automatisch angelegt. Wer die App öffnet, erfasst unter dem eigenen Namen.</p>
      <div class="list">${S.persons.map((p) => `<div class="setting-row ${p.active ? "" : "inactive"}" data-person="${p.id}">
        ${avatar(p)}<input type="text" value="${esc(p.name)}" data-pname aria-label="Name">
        <div class="swatches">${[1, 2, 3, 4, 5, 6, 7, 8].map((c) => `<button class="swatch" style="background:var(--series-${c})" data-color="${c}" aria-pressed="${p.color === c}" aria-label="Farbe ${c}"></button>`).join("")}</div>
        <span class="muted small">${p.ha_user_id ? "HA-Konto" : "ohne Konto"}</span>
        <label class="toggle"><input type="checkbox" data-pactive ${p.active ? "checked" : ""}> aktiv</label>
      </div>`).join("")}</div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Aufgaben</h2><div class="row-btns"><button class="btn ghost sm" data-templates>Aus Vorlagen …</button><button class="btn ghost sm" data-add-task>+ Aufgabe</button></div></div>
      ${[...S.categories, null].map((c) => {
        const list = S.tasks.filter((t) => t.active && (c ? t.category_id === c.id : !category(t.category_id)));
        if (!list.length && !c) return "";
        return `<div class="cat-block"><div class="cat-title">${c ? `<span>${esc(c.icon)} ${esc(c.name)}</span><button class="icon-btn" data-edit-cat="${c.id}" aria-label="Kategorie bearbeiten">✎</button>` : "<span>✨ Ohne Kategorie</span>"}</div>
          <div class="list">${list.map(taskSettingRow).join("") || `<div class="muted small pad">Keine Aufgaben</div>`}</div></div>`;
      }).join("")}
      <button class="btn ghost sm" data-add-cat>+ Kategorie</button>
      ${archived.length ? `<details class="archived"><summary>Archiviert (${archived.length})</summary><div class="list">${archived.map(taskSettingRow).join("")}</div></details>` : ""}
    </section>
    ${S.ha ? notifyCard() : ""}
    <section class="card">
      <h2>Daten</h2>
      <p class="muted">Alle Einträge liegen in einer SQLite-Datenbank im App-Ordner und sind Teil der Home-Assistant-Backups. Gelöschte Einträge bleiben erhalten und lassen sich im Verlauf wiederherstellen.</p>
      <div class="row-btns"><button class="btn ghost" data-export="json">Export JSON</button><button class="btn ghost" data-export="csv">Export CSV</button><label class="btn ghost">Import JSON<input type="file" accept=".json,application/json" data-import hidden></label></div>
    </section>`;

  el.querySelectorAll("[data-person]").forEach((row) => {
    const id = +row.dataset.person;
    const save = async (body) => { try { await api(`persons/${id}`, { method: "PATCH", body }); await loadState(); renderSettings(el); } catch (e) { fail(e); } };
    $("[data-pname]", row).addEventListener("change", (e) => save({ name: e.target.value }));
    $("[data-pactive]", row).addEventListener("change", (e) => save({ active: e.target.checked ? 1 : 0 }));
    row.querySelectorAll("[data-color]").forEach((b) => b.addEventListener("click", () => save({ color: +b.dataset.color })));
  });
  $("[data-add-person]", el).addEventListener("click", async () => {
    const name = prompt("Name der Person");
    if (!name) return;
    try { await api("persons", { method: "POST", body: { name } }); await loadState(); renderSettings(el); } catch (e) { fail(e); }
  });
  bindNotifyCard(el);
  el.querySelectorAll("[data-task-edit]").forEach((b) => b.addEventListener("click", () => openTaskEditor(task(+b.dataset.taskEdit))));
  $("[data-add-task]", el).addEventListener("click", () => openTaskEditor(null));
  $("[data-templates]", el).addEventListener("click", () => openTemplates());
  $("[data-add-cat]", el).addEventListener("click", () => openCategoryEditor(null));
  el.querySelectorAll("[data-edit-cat]").forEach((b) => b.addEventListener("click", () => openCategoryEditor(category(+b.dataset.editCat))));
  el.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", () => exportData(b.dataset.export)));
  $("[data-import]", el).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (!confirm(`Alle aktuellen Daten durch den Export vom ${new Date(data.exported_at).toLocaleString("de-DE")} ersetzen? (${data.completions?.length ?? 0} Einträge)`)) return;
      const r = await api("import", { method: "POST", body: data });
      toast(`Import fertig: ${r.completions} Einträge, ${r.tasks} Aufgaben`);
      await loadState();
      render();
    } catch (err) { fail(err); }
  });
}

function taskSettingRow(t) {
  return `<button class="setting-row task-row" data-task-edit="${t.id}">
    <span class="ticon sm">${esc(t.icon)}</span><span class="grow">${esc(t.name)}</span>
    <span class="muted small ha-flags">${t.sensor ? '<span title="Fällig nach Sensor">📡</span>' : ""}${t.action ? '<span title="Aktion beim Abhaken">⚡</span>' : ""}${t.notify ? '<span title="Benachrichtigung">🔔</span>' : ""}</span>
    <span class="muted small" ${learnedRhythm(t) ? `title="Gelernt aus ${t.learned_interval_from} Abständen, Standard: ${intervalName(t.interval_days)}"` : ""}>${interval(t) ? `${learnedRhythm(t) ? "⌀ " : ""}${intervalName(interval(t))}` : "—"}</span>
    <span class="muted small num" ${t.learned_minutes != null ? `title="Gelernt aus ${t.learned_from} Einträgen, Standard ${t.minutes} min"` : ""}>${t.learned_minutes != null ? "⌀ " : ""}${fmtMin(effort(t))}</span>
  </button>`;
}

const LEARN_WINDOW = 10;
const LEARN_MIN = 3;

function learnInfo(t) {
  if (t.learned_minutes == null) {
    const left = LEARN_MIN - t.learned_from;
    return `<div class="learn muted small">Noch ${plural(left, "Eintrag", "Einträge")} bis zum gelernten Aufwand – bis dahin gilt der Standard.</div>`;
  }
  const diff = t.learned_minutes - t.minutes;
  const trend = diff === 0 ? "entspricht dem Standard" : `${diff > 0 ? "+" : "−"}${fmtMin(Math.abs(diff))} ggü. Standard`;
  return `<div class="learn"><span>⌀ <b>${fmtMin(t.learned_minutes)}</b> aus den letzten ${t.learned_from} Einträgen · ${trend}</span>
    ${diff ? `<button type="button" class="btn ghost sm" data-adopt="${t.learned_minutes}">Als Standard übernehmen</button>` : ""}</div>`;
}

const learnedRhythm = (t) => t.interval_days && t.learned_interval;

function learnIntervalInfo(t) {
  if (t.learned_interval == null) {
    const left = 3 - t.learned_interval_from + (t.count ? 0 : 1);
    return `<div class="learn muted small">Noch ${plural(left, "Erledigung", "Erledigungen")} an verschiedenen Tagen bis zum gelernten Rhythmus${t.interval_days ? " – bis dahin gilt der Standard" : ""}.</div>`;
  }
  const n = t.learned_interval;
  const from = `aus den letzten ${t.learned_interval_from} Abständen`;
  if (!t.interval_days) {
    return `<div class="learn"><span>Ihr erledigt das etwa <b>${intervalName(n).charAt(0).toLowerCase()}${intervalName(n).slice(1)}</b> (${from})</span>
      <button type="button" class="btn ghost sm" data-adopt-interval="${n}">Als Rhythmus übernehmen</button></div>`;
  }
  const diff = n - t.interval_days;
  const trend = diff === 0 ? "entspricht dem Standard" : `${diff > 0 ? "+" : "−"}${plural(Math.abs(diff), "Tag", "Tage")} ggü. Standard`;
  return `<div class="learn"><span>⌀ <b>${intervalName(n)}</b> ${from} · ${trend}</span>
    ${diff ? `<button type="button" class="btn ghost sm" data-adopt-interval="${n}">Als Standard übernehmen</button>` : ""}</div>`;
}

function openTaskEditor(t) {
  const isNew = !t;
  t = t || { name: "", icon: "", category_id: S.categories[0]?.id, minutes: 10, interval_days: null, active: 1 };
  const custom = t.interval_days && !INTERVALS.some(([d]) => d === t.interval_days);
  openSheet(`
    <div class="sheet-head"><span class="ticon big">${esc(t.icon || "•")}</span><div><h2>${isNew ? "Neue Aufgabe" : "Aufgabe bearbeiten"}</h2>${isNew ? "" : `<div class="muted">${plural(t.count, "Mal", "Mal")} erledigt</div>`}</div><button class="icon-btn" data-close aria-label="Schließen">✕</button></div>
    <div class="grid2">
      <label class="field"><span>Symbol</span><input type="text" name="icon" value="${esc(t.icon)}" maxlength="4" placeholder="🧹" class="emoji-in"></label>
      <label class="field grow"><span>Name</span><input type="text" name="name" value="${esc(t.name)}" required></label>
    </div>
    <div class="grid2">
      <label class="field"><span>Kategorie</span><select name="category">${S.categories.map((c) => `<option value="${c.id}" ${c.id === t.category_id ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)}</option>`).join("")}<option value="" ${category(t.category_id) ? "" : "selected"}>Ohne Kategorie</option></select></label>
      ${S.areas.length ? `<label class="field"><span>Raum</span><select name="area"><option value="">Kein Raum</option>${S.areas.map((a) => `<option value="${esc(a.id)}" ${a.id === t.area_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label>` : ""}
    </div>
    <div class="grid2">
      <label class="field"><span>Rhythmus</span><select name="interval">${INTERVALS.map(([d, l]) => `<option value="${d}" ${(t.interval_days || 0) === d ? "selected" : ""}>${l}</option>`).join("")}<option value="custom" ${custom ? "selected" : ""}>Eigener …</option></select></label>
      <label class="field" data-custom ${custom ? "" : "hidden"}><span>Alle … Tage</span><input type="number" name="days" min="1" value="${t.interval_days || 10}"></label>
    </div>
    ${isNew ? "" : learnIntervalInfo(t)}
    <label class="field"><span>Standardaufwand</span><div class="stepper"><button type="button" data-step="-5">−</button><input type="number" name="minutes" min="0" value="${t.minutes}" inputmode="numeric"><span>min</span><button type="button" data-step="5">+</button></div></label>
    ${isNew ? "" : learnInfo(t)}
    ${haBlock(t)}
    <p class="muted small">Beim Abhaken wird der Median der letzten ${LEARN_WINDOW} Einträge vorgeschlagen, sobald es mindestens ${LEARN_MIN} gibt – vorher der Standard. Wer beim Abhaken den Aufwand korrigiert, bringt der App also bei, wie lange es wirklich dauert. Genauso lernt der Rhythmus aus den Abständen zwischen den Erledigungen, auch wenn ihr etwas vor der Fälligkeit erledigt; ohne festen Rhythmus bleibt er ein Vorschlag. Ein neuer Standard setzt das jeweilige Lernen zurück; bestehende Einträge bleiben unverändert.</p>
    <div class="sheet-actions">
      ${isNew ? "" : t.active ? `<button type="button" class="btn danger" data-archive>${t.count ? "Archivieren" : "Löschen"}</button>` : `<button type="button" class="btn ghost" data-reactivate>Reaktivieren</button>`}
      <button type="submit" class="btn primary">Speichern</button>
    </div>`, (dlg) => {
    const form = $("form", dlg);
    bindHaBlock(dlg);
    form.interval.addEventListener("change", () => { $("[data-custom]", dlg).hidden = form.interval.value !== "custom"; });
    $("[data-adopt]", dlg)?.addEventListener("click", (e) => { form.minutes.value = e.target.dataset.adopt; });
    $("[data-adopt-interval]", dlg)?.addEventListener("click", (e) => {
      const n = +e.target.dataset.adoptInterval;
      const preset = INTERVALS.some(([d]) => d === n);
      form.interval.value = preset ? String(n) : "custom";
      form.days.value = n;
      $("[data-custom]", dlg).hidden = preset;
    });
    form.icon.addEventListener("input", () => { $(".sheet-head .ticon", dlg).textContent = form.icon.value || "•"; });
    dlg.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => { form.minutes.value = Math.max(0, (+form.minutes.value || 0) + +b.dataset.step); }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {
        name: form.name.value, icon: form.icon.value.trim(), minutes: +form.minutes.value || 0,
        category_id: form.category.value ? +form.category.value : null,
        ...(form.area ? { area_id: form.area.value || null } : {}),
        interval_days: form.interval.value === "custom" ? +form.days.value || null : +form.interval.value || null,
        ...haValues(dlg, t),
      };
      try {
        await api(isNew ? "tasks" : `tasks/${t.id}`, { method: isNew ? "POST" : "PATCH", body });
        dlg.close();
        await loadState();
        render();
      } catch (err) { fail(err); }
    });
    $("[data-archive]", dlg)?.addEventListener("click", async () => {
      const r = await api(`tasks/${t.id}`, { method: "DELETE" });
      dlg.close();
      toast(r.result === "archived" ? `<b>${esc(t.name)}</b> archiviert` : `<b>${esc(t.name)}</b> gelöscht`,
        r.result === "archived" ? { label: "Rückgängig", run: async () => { await api(`tasks/${t.id}`, { method: "PATCH", body: { active: 1 } }); await loadState(); render(); } } : undefined);
      await loadState();
      render();
    });
    $("[data-reactivate]", dlg)?.addEventListener("click", async () => {
      await api(`tasks/${t.id}`, { method: "PATCH", body: { active: 1 } });
      dlg.close();
      await loadState();
      render();
    });
  });
}

// ---------------------------------------------------------------- Vorlagen

async function openTemplates() {
  let data;
  try { data = await api("templates"); } catch (e) { return fail(e); }
  const items = [];
  const row = (it) => {
    const idx = items.push(it) - 1;
    const meta = [it.interval_days ? intervalName(it.interval_days) : "ohne Rhythmus", fmtMin(it.minutes)];
    const what = it.status === "update"
      ? `ergänzt „${esc(it.existing_name)}“ um ${Object.keys(it.update).map((k) => ({ sensor: "Sensor-Regel", action: "Aktion", area_id: `Raum ${esc(it.area_name || "")}` }[k])).join(" und ")}`
      : it.status === "exists" ? `schon vorhanden${it.existing_name && it.existing_name !== it.name ? ` als „${esc(it.existing_name)}“` : ""}` : meta.join(" · ");
    return `<label class="tpl-row ${it.status}">
      <input type="checkbox" data-tpl="${idx}" ${it.status === "exists" ? "disabled" : ""}>
      <span class="ticon sm">${esc(it.icon)}</span>
      <span class="tpl-body"><span class="tpl-name">${esc(it.name)}${it.sensor ? " 📡" : ""}${it.action ? " ⚡" : ""}</span>
        <span class="muted small">${what}</span>${it.reason ? `<span class="muted small">${esc(it.reason)}</span>` : ""}</span>
    </label>`;
  };
  const open = (list) => list.filter((i) => i.status !== "exists").length;
  openSheet(`
    <div class="sheet-head"><span class="ticon big">📚</span><div><h2>Aufgaben aus Vorlagen</h2><div class="muted">Anhaken, was zu euch passt – Rhythmus und Aufwand lassen sich danach anpassen</div></div><button class="icon-btn" data-close aria-label="Schließen">✕</button></div>
    ${data.devices.length ? `<section class="tpl-group"><h3>Eure Geräte <span class="muted">aus Home Assistant erkannt</span></h3>${data.devices.map(row).join("")}</section>` : ""}
    ${data.rooms.length ? `<section class="tpl-group"><h3>Eure Räume <span class="muted">Aufgaben werden dem Raum zugeordnet</span></h3>
      ${data.rooms.map((r) => `<details class="tpl-room"><summary>📍 ${esc(r.name)} <span class="muted small">${open(r.items)} Vorschläge</span></summary>${r.items.map(row).join("")}</details>`).join("")}</section>` : ""}
    <section class="tpl-group"><h3>Alle Vorlagen</h3>
      ${data.catalog.map((c) => `<details class="tpl-room"><summary>${esc(c.icon)} ${esc(c.name)} <span class="muted small">${open(c.items)} neu</span></summary>${c.items.map(row).join("")}</details>`).join("")}</section>
    <div class="sheet-actions tpl-actions"><button type="submit" class="btn primary" data-apply disabled>Übernehmen</button></div>
  `, (dlg) => {
    const form = $("form", dlg);
    const selected = () => [...dlg.querySelectorAll("[data-tpl]:checked")].map((c) => items[+c.dataset.tpl]);
    form.addEventListener("change", () => {
      const n = selected().length;
      $("[data-apply]", dlg).disabled = !n;
      $("[data-apply]", dlg).textContent = n ? `${n} übernehmen` : "Übernehmen";
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      // Dieselbe Vorlage aus Raum und Katalog nur einmal anlegen, die Variante mit Raum gewinnt
      const byKey = new Map();
      selected().forEach((it) => {
        const key = it.existing_id ? `u${it.existing_id}` : `n${it.name.toLowerCase()}`;
        const prev = byKey.get(key);
        if (!prev) byKey.set(key, it);
        else if (it.existing_id) byKey.set(key, { ...prev, update: { ...prev.update, ...it.update } });
        else if (it.area_id) byKey.set(key, it);
      });
      try {
        const r = await api("templates/apply", { method: "POST", body: { items: [...byKey.values()] } });
        dlg.close();
        toast([r.created && `${plural(r.created, "Aufgabe", "Aufgaben")} angelegt`, r.updated && `${r.updated} ergänzt`].filter(Boolean).join(", ") || "Nichts geändert");
        S.catalog = null;
        await loadState();
        render();
      } catch (err) { fail(err); }
    });
  });
}

// ---------------------------------------------------------------- Home Assistant: Sensor-Regel, Aktion, Benachrichtigung

const SENSOR_MODES = [
  ["", "Aus"], ["below", "Fällig, wenn ein Wert auf die Grenze fällt"], ["above", "Fällig, wenn ein Wert die Grenze erreicht"],
  ["increase", "Fällig nach Zuwachs seit dem Abhaken"],
];
const SERVICES = ["button.press", "input_button.press", "counter.reset", "script.turn_on", "switch.turn_off", "switch.turn_on"];

async function loadCatalog() {
  if (!S.catalog) S.catalog = await api("ha/catalog").catch(() => ({ entities: [], notify: [] }));
  return S.catalog;
}

function entityHint(id) {
  const e = S.catalog?.entities.find((x) => x.id === id);
  if (!e) return id ? "unbekannt oder deaktiviert" : "";
  const num = e.state !== "" && !isNaN(+e.state) ? (+e.state).toLocaleString("de-DE", { maximumFractionDigits: 2 }) : e.state;
  return `${esc(e.name)} · ${esc(num)}${e.unit ? ` ${esc(e.unit)}` : ""}`;
}

function entityRow(id = "") {
  return `<div class="entity-row"><div class="entity-in"><input type="text" list="ha-entities-all" value="${esc(id)}" placeholder="sensor.…" autocapitalize="off" spellcheck="false"><small class="muted">${entityHint(id)}</small></div>
    <button type="button" class="icon-btn" data-del-entity aria-label="Entität entfernen">✕</button></div>`;
}

function haBlock(t) {
  const notifiable = activePersons();
  const withService = notifiable.filter((p) => p.notify_service).map((p) => String(p.id));
  const selected = t.notify ? t.notify.map(String) : withService.length ? withService : notifiable.map((p) => String(p.id));
  const sens = t.sensor || { mode: "", entities: [], threshold: 0 };
  return `<details class="ha-block" ${t.sensor || t.action || t.notify ? "open" : ""}>
    <summary>Home Assistant <span class="muted small">Sensor, Aktion, Benachrichtigung</span></summary>
    <label class="field"><span>Fälligkeit aus Sensoren</span><select name="smode">${SENSOR_MODES.map(([v, l]) => `<option value="${v}" ${sens.mode === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <div data-sensor ${sens.mode ? "" : "hidden"}>
      <div class="field"><span>Entitäten <em class="muted">eine reicht, mehrere: fällig, sobald eine greift</em></span>
        <div data-entities="sensor">${(sens.entities.length ? sens.entities : [""]).map(entityRow).join("")}</div>
        <button type="button" class="btn ghost sm" data-add-entity="sensor">+ Entität</button></div>
      <label class="field"><span data-thr-label>Grenze</span><input type="number" step="any" name="sthreshold" value="${sens.threshold ?? 0}"></label>
      ${t.sensor_state ? `<div class="learn"><span>Jetzt: <b>${esc(t.sensor_state.label)}</b></span></div>` : ""}
    </div>
    <label class="field"><span>Beim Abhaken ausführen</span><input type="text" name="aservice" list="ha-services" value="${esc(t.action?.service || "")}" placeholder="z. B. button.press – leer = nichts" autocapitalize="off" spellcheck="false"></label>
    <div class="field" data-action ${t.action ? "" : "hidden"}><span>Ziel-Entitäten <em class="muted">optional</em></span>
      <div data-entities="action">${(t.action?.entities?.length ? t.action.entities : [""]).map(entityRow).join("")}</div>
      <button type="button" class="btn ghost sm" data-add-entity="action">+ Entität</button></div>
    <div class="field"><span>Benachrichtigen</span>
      <label class="toggle"><input type="checkbox" name="notify" ${t.notify ? "checked" : ""}> Wenn fällig, täglich um ${esc(S.settings.notify_time || "18:00")} Uhr</label>
      <div data-notify ${t.notify ? "" : "hidden"}>${chips("notify", notifiable.map((p) => ({ value: String(p.id), html: `${avatar(p, "sm")}${esc(p.name)}${p.notify_service ? "" : ' <em class="muted">ohne Dienst</em>'}` })), selected)}</div>
    </div>
    <datalist id="ha-entities-all"></datalist>
    <datalist id="ha-services">${SERVICES.map((x) => `<option value="${x}">`).join("")}</datalist>
  </details>`;
}

function bindHaBlock(dlg) {
  const form = $("form", dlg);
  bindChips(dlg);
  const thrLabel = () => {
    $("[data-thr-label]", dlg).textContent = { below: "Fällig bei höchstens", above: "Fällig ab", increase: "Fällig nach Zuwachs von" }[form.smode.value] || "Grenze";
  };
  thrLabel();
  form.smode.addEventListener("change", () => { $("[data-sensor]", dlg).hidden = !form.smode.value; thrLabel(); });
  form.aservice.addEventListener("input", () => { $("[data-action]", dlg).hidden = !form.aservice.value.trim(); });
  form.notify.addEventListener("change", () => { $("[data-notify]", dlg).hidden = !form.notify.checked; });
  const block = $(".ha-block", dlg);
  block.addEventListener("click", (e) => {
    const add = e.target.closest("[data-add-entity]");
    if (add) $(`[data-entities="${add.dataset.addEntity}"]`, dlg).insertAdjacentHTML("beforeend", entityRow());
    const del = e.target.closest("[data-del-entity]");
    if (del) del.closest(".entity-row").remove();
  });
  block.addEventListener("input", (e) => {
    if (e.target.matches(".entity-in input")) e.target.nextElementSibling.innerHTML = entityHint(e.target.value.trim());
  });
  loadCatalog().then((c) => {
    $("#ha-entities-all", dlg).innerHTML = c.entities.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join("");
    const scripts = c.entities.filter((e) => e.id.startsWith("script.")).map((e) => `<option value="${e.id}">${esc(e.name)}</option>`);
    $("#ha-services", dlg).insertAdjacentHTML("beforeend", scripts.join(""));
    dlg.querySelectorAll(".entity-in input").forEach((i) => { i.nextElementSibling.innerHTML = entityHint(i.value.trim()); });
  });
}

function haValues(dlg, t) {
  const form = $("form", dlg);
  const ids = (kind) => [...dlg.querySelectorAll(`[data-entities="${kind}"] .entity-in input`)].map((i) => i.value.trim()).filter(Boolean);
  const mode = form.smode.value;
  const service = form.aservice.value.trim();
  return {
    sensor: mode && ids("sensor").length ? { mode, entities: ids("sensor"), threshold: +form.sthreshold.value || 0, base: t.sensor?.base } : null,
    action: service ? { service, entities: ids("action") } : null,
    notify: form.notify.checked ? chipValues(dlg, "notify").map(Number) : null,
  };
}

function notifyCard() {
  const st = S.settings;
  return `<section class="card" data-notify-card>
    <h2>Benachrichtigungen</h2>
    <p class="muted">Aufgaben mit 🔔 melden sich einmal am Tag, wenn sie fällig sind. „Erledigt ✅“ in der Nachricht hakt sie für dich ab und führt die Aktion der Aufgabe aus.</p>
    <div class="grid2">
      <label class="field"><span>Uhrzeit</span><input type="time" name="notify_time" value="${esc(st.notify_time)}"></label>
      <label class="field"><span>Nur wenn an</span><input type="text" name="notify_require_on" list="ha-entities" value="${esc(st.notify_require_on)}" placeholder="binary_sensor.jemand_zu_hause" autocapitalize="off" spellcheck="false"></label>
      <label class="field"><span>Nicht wenn an</span><input type="text" name="notify_require_off" list="ha-entities" value="${esc(st.notify_require_off)}" placeholder="calendar.urlaub" autocapitalize="off" spellcheck="false"></label>
    </div>
    <label class="toggle notify-done"><input type="checkbox" data-notify-done ${st.notify_done === "1" ? "checked" : ""}> Alle anderen benachrichtigen, wenn jemand eine Aufgabe erledigt</label>
    <div class="list">${activePersons().map((p) => `<div class="setting-row" data-notify-person="${p.id}">
      ${avatar(p)}<span class="notify-name">${esc(p.name)}</span>
      <input type="text" list="ha-notify" value="${esc(p.notify_service || "")}" placeholder="notify.mobile_app_…" aria-label="Benachrichtigungsdienst für ${esc(p.name)}" autocapitalize="off" spellcheck="false">
      <button class="btn ghost sm" data-test ${p.notify_service ? "" : "disabled"}>Test</button>
    </div>`).join("")}</div>
    <datalist id="ha-notify"></datalist><datalist id="ha-entities"></datalist>
  </section>`;
}

function bindNotifyCard(el) {
  const card = $("[data-notify-card]", el);
  if (!card) return;
  card.querySelectorAll("input[name]").forEach((i) => i.addEventListener("change", async () => {
    try { S.settings = await api("settings", { method: "PATCH", body: { [i.name]: i.value } }); toast("Gespeichert"); } catch (e) { fail(e); }
  }));
  $("[data-notify-done]", card).addEventListener("change", async (e) => {
    try { S.settings = await api("settings", { method: "PATCH", body: { notify_done: e.target.checked ? "1" : "" } }); toast("Gespeichert"); } catch (err) { fail(err); }
  });
  card.querySelectorAll("[data-notify-person]").forEach((row) => {
    const id = +row.dataset.notifyPerson;
    $("input", row).addEventListener("change", async (e) => {
      try { await api(`persons/${id}`, { method: "PATCH", body: { notify_service: e.target.value.trim() } }); await loadState(); renderSettings(el); } catch (err) { fail(err); }
    });
    $("[data-test]", row).addEventListener("click", async () => {
      try { await api(`persons/${id}/test`, { method: "POST" }); toast("Testnachricht verschickt"); } catch (e) { fail(e); }
    });
  });
  loadCatalog().then((c) => {
    $("#ha-notify", card).innerHTML = c.notify.map((n) => `<option value="${n}">`).join("");
    $("#ha-entities", card).innerHTML = c.entities.filter((e) => /^(binary_sensor|input_boolean|calendar|person|group|switch)\./.test(e.id))
      .map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join("");
  });
}

function openCategoryEditor(c) {
  const isNew = !c;
  c = c || { name: "", icon: "" };
  openSheet(`
    <div class="sheet-head"><span class="ticon big">${esc(c.icon || "•")}</span><div><h2>${isNew ? "Neue Kategorie" : "Kategorie bearbeiten"}</h2></div><button class="icon-btn" data-close aria-label="Schließen">✕</button></div>
    <div class="grid2">
      <label class="field"><span>Symbol</span><input type="text" name="icon" value="${esc(c.icon)}" maxlength="4" class="emoji-in"></label>
      <label class="field grow"><span>Name</span><input type="text" name="name" value="${esc(c.name)}" required></label>
    </div>
    <div class="sheet-actions">
      ${isNew ? "" : `<button type="button" class="btn danger" data-del-cat>Löschen</button>`}
      <button type="submit" class="btn primary">Speichern</button>
    </div>`, (dlg) => {
    const form = $("form", dlg);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(isNew ? "categories" : `categories/${c.id}`, { method: isNew ? "POST" : "PATCH", body: { name: form.name.value, icon: form.icon.value.trim() } });
        dlg.close();
        await loadState();
        render();
      } catch (err) { fail(err); }
    });
    $("[data-del-cat]", dlg)?.addEventListener("click", async () => {
      if (!confirm(`Kategorie „${c.name}“ löschen? Die Aufgaben bleiben erhalten und landen unter „Ohne Kategorie“.`)) return;
      await api(`categories/${c.id}`, { method: "DELETE" });
      dlg.close();
      await loadState();
      render();
    });
  });
}

async function exportData(kind) {
  const data = await api("export");
  let blob;
  if (kind === "json") {
    blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  } else {
    const names = new Map(data.persons.map((p) => [p.id, p.name]));
    const tasks = new Map(data.tasks.map((t) => [t.id, t]));
    const cats = new Map(data.categories.map((c) => [c.id, c.name]));
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [["id", "zeitpunkt", "aufgabe", "kategorie", "personen", "minuten", "notiz", "geloescht"].join(";")];
    for (const c of data.completions) {
      const t = tasks.get(c.task_id) || {};
      lines.push([c.id, c.done_at, q(t.name), q(cats.get(t.category_id)), q(c.person_ids.map((i) => names.get(i)).join(", ")),
        String(c.minutes).replace(".", ","), q(c.note), c.deleted_at ? "ja" : ""].join(";"));
    }
    blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv" });
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `haushalt-${dayKey(new Date())}.${kind}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------------------------------------------------------------- Theme aus Home Assistant übernehmen

function syncTheme() {
  try {
    const ha = window.parent !== window && window.parent.document.querySelector("home-assistant");
    const dark = ha?.hass?.themes?.darkMode;
    if (typeof dark === "boolean") document.documentElement.dataset.theme = dark ? "dark" : "light";
  } catch { /* nicht im HA-Frame */ }
}

// ---------------------------------------------------------------- Start

async function start() {
  syncTheme();
  setInterval(syncTheme, 3000);
  try {
    await loadState();
  } catch (e) {
    document.body.innerHTML = `<div class="empty-state">Haushalt konnte nicht geladen werden: ${esc(e.message)}</div>`;
    return;
  }
  frame();
  render();
  let w = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - w) < 40 || S.view !== "stats") return;
    w = window.innerWidth;
    clearTimeout(start.t);
    start.t = setTimeout(render, 200);
  });
  // Andere Person hat etwas abgehakt → beim Zurückkehren in die App aktualisieren
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || $("#sheet").open) return;
    await loadState();
    render();
  });
}

start();
