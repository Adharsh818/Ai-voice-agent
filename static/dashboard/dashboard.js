// Pearl Dental staff dashboard. Vanilla JS, no build step, no CDN.
//
// Every value from the server goes into the page with textContent, never
// innerHTML, so a caller who gives "<script>" as their name stays plain text.
// All times are shown in the clinic's timezone, whatever the browser's is.

let TZ = "Asia/Kolkata";
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ helpers
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function api(path, { method = "GET", body } = {}) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (res.status === 401 || res.status === 503) {
    location.replace("/dashboard/login");
    throw new ApiError(res.status, "Please log in.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    throw new ApiError(res.status, (data && (data.message || data.detail)) || `Request failed (${res.status})`, data);
  }
  return data;
}

function fmt(value, options) {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-IN", { timeZone: TZ, ...options }).format(date);
}
const fmtDateTime = (v) => fmt(v, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const fmtTime = (v) => fmt(v, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

function clinicToday() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function shiftDay(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

function prettyDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

function length(startIso, endIso) {
  if (!startIso) return "–";
  const secs = Math.max(0, Math.round(((endIso ? new Date(endIso) : new Date()) - new Date(startIso)) / 1000));
  const m = Math.floor(secs / 60);
  return m ? `${m}m ${secs % 60}s` : `${secs}s`;
}

function ms(value) {
  return value === null || value === undefined ? "–" : Math.round(value).toLocaleString("en-IN");
}

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

function toast(message, kind = "") {
  const node = el("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status", text: message });
  $("toasts").append(node);
  setTimeout(() => node.remove(), kind === "urgent" ? 12000 : 4500);
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function store(key, value) {
  try { localStorage.setItem(`emma-dashboard:${key}`, value); } catch { /* storage blocked */ }
}
function recall(key) {
  try { return localStorage.getItem(`emma-dashboard:${key}`); } catch { return null; }
}

function emptyRow(colspan, text) {
  return el("tr", {}, el("td", { colspan, class: "empty", text }));
}

const STATUS_LABEL = {
  booked: "Booked", cancelled: "Cancelled", needs_reschedule: "Needs reschedule",
  completed: "Completed", no_show: "No-show",
};
const KIND_LABEL = {
  callback: "Callback", emergency: "Emergency", red_flag: "Red flag", recovery_failed: "Recovery call failed",
  escalation: "Escalation", language: "Language", abandoned: "Abandoned call",
};
const ROLE_LABEL = { caller: "Caller", emma: "Emma", operator: "Staff" };

// ------------------------------------------------------------------ dialogs
document.querySelectorAll("dialog [data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

function confirmAction(title, text, okLabel = "Confirm") {
  const dialog = $("dlg-confirm");
  $("dlg-confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = okLabel;
  return new Promise((resolve) => {
    const done = () => {
      dialog.removeEventListener("close", done);
      resolve(dialog.returnValue === "ok");
    };
    dialog.returnValue = "";
    $("confirm-ok").value = "ok";
    dialog.addEventListener("close", done);
    dialog.showModal();
  });
}

function showError(box, message) {
  box.textContent = message || "";
  box.hidden = !message;
}

// ------------------------------------------------------------------ catalogue and overview
const catalog = { services: [], doctors: [], branches: [] };

async function loadCatalog() {
  Object.assign(catalog, await api("/dashboard/api/catalog"));
  const branchSelect = $("appt-branch");
  for (const b of catalog.branches) branchSelect.append(el("option", { value: b.id, text: b.name }));
  fillDoctorFilter();
}

function fillDoctorFilter() {
  const select = $("appt-doctor");
  const branch = Number($("appt-branch").value) || null;
  const current = select.value;
  select.replaceChildren(el("option", { value: "", text: "All doctors" }));
  for (const d of catalog.doctors.filter((doc) => !branch || doc.branch_id === branch)) {
    select.append(el("option", { value: d.id, text: branch ? d.name : `${d.name} (${d.branch})` }));
  }
  select.value = [...select.options].some((o) => o.value === current) ? current : "";
}

async function refreshOverview() {
  let data;
  try { data = await api("/dashboard/api/overview"); } catch { return; }
  if (data.timezone) TZ = data.timezone;
  const openTasks = Object.values(data.tasks).reduce((a, b) => a + b, 0);
  const urgent = data.tasks.urgent || 0;
  const chipTasks = $("chip-tasks");
  chipTasks.replaceChildren("Tasks ", el("strong", { text: urgent ? `${openTasks} · ${urgent} urgent` : String(openTasks) }));
  chipTasks.classList.toggle("alert", urgent > 0);
  const count = $("tab-task-count");
  count.textContent = String(openTasks);
  count.hidden = openTasks === 0;
  const failed = data.sync.failed || 0;
  const chipSync = $("chip-sync");
  chipSync.hidden = failed === 0;
  chipSync.classList.toggle("alert", failed > 0);
  chipSync.replaceChildren("Calendar ", el("strong", { text: `${failed} failed` }));
  updateCallChip(data.call);
}

function updateCallChip(call) {
  const chip = $("chip-call");
  const busy = call && call.busy;
  chip.classList.toggle("live", Boolean(busy));
  chip.replaceChildren(el("span", { class: "dot" }), el("span", { text: busy ? "On a call" : "No call" }));
}

// ------------------------------------------------------------------ tabs
const loaders = {
  live: () => renderLive(),
  appointments: () => loadAppointments(),
  tasks: () => loadTasks(),
  calls: () => loadCalls(),
  recovery: () => loadRecovery(),
  system: () => loadSystem(),
};
let currentTab = "live";

function showTab(name) {
  if (!loaders[name]) name = "live";
  currentTab = name;
  document.querySelectorAll(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
  for (const key of Object.keys(loaders)) $(`view-${key}`).hidden = key !== name;
  store("tab", name);
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  loaders[name]();
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));
document.querySelectorAll("[data-go]").forEach((chip) => chip.addEventListener("click", () => showTab(chip.dataset.go)));

// ------------------------------------------------------------------ live call
const live = {
  callId: null, started: null, ended: false, state: "idle", lines: [], interim: "",
  turns: new Map(), goal: null, tier: null, entities: {}, outcome: null, lastSeq: 0,
};

function resetLive(callId = null) {
  Object.assign(live, {
    callId, started: callId ? new Date() : null, ended: false, state: callId ? "listening" : "idle", lines: [],
    interim: "", turns: new Map(), goal: null, tier: null, entities: {}, outcome: null, operator: false,
  });
}

function addCaption(who, text, final) {
  if (!text) return;
  if (who === "emma") {
    const last = live.lines[live.lines.length - 1];
    if (last) last.closed = true;
    live.lines.push({ who: "emma", text, closed: true });
    return;
  }
  if (!final) {
    live.interim = text;
    return;
  }
  live.interim = "";
  const last = live.lines[live.lines.length - 1];
  if (last && last.who === "caller" && !last.closed) last.text = `${last.text} ${text}`;
  else live.lines.push({ who: "caller", text, closed: false });
}

function handleEvent(event) {
  if (event.seq && event.seq <= live.lastSeq) return;
  if (event.seq) live.lastSeq = event.seq;
  switch (event.type) {
    case "call_started":
      resetLive(event.call_id);
      updateCallChip({ busy: true });
      break;
    case "caption":
      if (!live.callId) resetLive(event.call_id);
      addCaption(event.who === "emma" ? "emma" : "caller", event.text, event.final);
      break;
    case "state":
      if (!live.ended) live.state = event.state;
      break;
    case "turn":
      if (event.phase === "start" && !live.ended) live.state = "speaking";
      break;
    case "metrics":
      live.turns.set(event.turn, event);
      if (event.tier !== undefined && event.tier !== null) live.tier = event.tier;
      if (event.goal_after || event.step_after) live.goal = event.goal_after || `step ${event.step_after}`;
      break;
    case "call_turn": {
      const meta = event.meta || {};
      if (meta.goal_after) live.goal = meta.goal_after;
      if (meta.tier !== undefined && meta.tier !== null) live.tier = meta.tier;
      if (meta.entities && typeof meta.entities === "object") Object.assign(live.entities, meta.entities);
      if (meta.action) {
        // Emma just booked, moved or cancelled something: the lists change too.
        live.outcome = meta.action;
        refreshSoon();
        if (currentTab === "appointments") reloadAppointmentsSoon();
      }
      break;
    }
    case "call_ended":
      live.ended = true;
      live.state = "ended";
      live.outcome = event.outcome;
      updateCallChip({ busy: false });
      refreshSoon();
      if (currentTab === "calls") loadCalls();
      if (currentTab === "appointments") reloadAppointmentsSoon();
      break;
    case "task_created":
      toast(`New ${event.priority === "normal" ? "" : `${event.priority} `}task: ${KIND_LABEL[event.kind] || event.kind}`,
        event.priority === "urgent" ? "urgent" : "");
      refreshSoon();
      if (currentTab === "tasks") loadTasks();
      break;
    case "task_updated":
      refreshSoon();
      if (currentTab === "tasks") loadTasks();
      break;
    case "appointment":
    case "sync":
      refreshSoon();
      if (currentTab === "appointments") reloadAppointmentsSoon();
      if (currentTab === "system") reloadSystemSoon();
      break;
    case "call_data_deleted":
      if (currentTab === "calls") loadCalls();
      break;
    case "operator":
      if (event.call_id === live.callId) live.operator = Boolean(event.active);
      break;
    case "recovery":
    case "ring":
    case "ring_ended":
      if (currentTab === "recovery") reloadRecoverySoon();
      if (event.type === "ring") toast(`Emma is calling ${event.to_name || "a patient"}…`);
      break;
    default:
      return;
  }
  if (currentTab === "live") renderLive();
}

function renderLive() {
  const hasCall = Boolean(live.callId);
  $("live-empty").hidden = hasCall;
  const pill = $("live-state");
  const label = { idle: "Idle", listening: "Listening", thinking: "Thinking", speaking: "Speaking", ended: "Call ended" };
  pill.className = `state-pill ${live.state}`;
  pill.textContent = label[live.state] || live.state;
  $("live-call").textContent = live.callId || "–";
  $("live-goal").textContent = live.goal || "–";
  $("live-tier").textContent = live.tier === null ? "–" : { 0: "0 · fast path", 1: "1 · language model", "-1": "no NLU" }[live.tier] ?? String(live.tier);
  $("live-outcome").textContent = live.outcome || (hasCall && !live.ended ? "in progress" : "–");
  renderDuration();
  const active = hasCall && !live.ended;
  $("live-controls").hidden = !active;
  $("op-takeover").hidden = live.operator;
  $("op-handback").hidden = !live.operator;
  $("op-form").hidden = !live.operator;

  const box = $("live-transcript");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  const nodes = live.lines.map((line) => el("div", { class: `line ${line.who}` },
    el("span", { class: "who", text: ROLE_LABEL[line.who] || line.who }),
    el("div", { class: "bubble", text: line.text })));
  if (live.interim) {
    nodes.push(el("div", { class: "line caller interim" }, el("span", { class: "who", text: "Caller (hearing…)" }),
      el("div", { class: "bubble", text: live.interim })));
  }
  box.replaceChildren(...nodes);
  if (nearBottom) box.scrollTop = box.scrollHeight;

  const entities = Object.entries(live.entities).filter(([, v]) => v !== null && v !== "" && v !== undefined);
  $("live-entities").replaceChildren(...entities.map(([k, v]) => el("span", { class: "entity" },
    el("b", { text: k.replace(/_/g, " ") }), typeof v === "object" ? JSON.stringify(v) : String(v))));

  const rows = [...live.turns.values()].sort((a, b) => b.turn - a.turn).slice(0, 30).map((t) => el("tr", {},
    el("td", { class: "num", text: t.turn }),
    el("td", { class: "num", text: t.tier ?? "–" }),
    el("td", { class: "num", text: ms(t.perceived_ms) }),
    el("td", { class: "num", text: ms(t.endpoint_ms) }),
    el("td", { class: "num", text: ms(t.nlu_ms) }),
    el("td", { class: "num", text: ms(t.first_audio_ms) })));
  $("live-turns").replaceChildren(...(rows.length ? rows : [emptyRow(6, "No turns yet")]));
}

async function liveAction(action, body = {}) {
  if (!live.callId) return null;
  try {
    return await api(`/dashboard/api/live/${encodeURIComponent(live.callId)}/${action}`, { method: "POST", body });
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
}

$("op-takeover").addEventListener("click", async () => {
  const res = await liveAction("takeover");
  if (res && res.ok) {
    live.operator = true;
    renderLive();
    $("op-text").focus();
  }
});
$("op-handback").addEventListener("click", async () => {
  const res = await liveAction("handback");
  if (res && res.ok) { live.operator = false; renderLive(); }
});
$("op-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("op-text").value.trim();
  if (!text) return;
  const res = await liveAction("say", { text });
  if (res && res.ok) $("op-text").value = "";
});
$("op-end").addEventListener("click", async () => {
  if (!await confirmAction("End this call?", "Emma says goodbye and promises a call back; a callback task is created.", "End call")) return;
  await liveAction("end", {});
});

function renderDuration() {
  $("live-duration").textContent = live.started ? (live.ended ? "ended" : length(live.started.toISOString())) : "–";
}
setInterval(() => { if (currentTab === "live" && live.callId && !live.ended) renderDuration(); }, 1000);

const EVENT_TYPES = ["call_started", "caption", "state", "turn", "metrics", "call_turn", "call_ended", "task_created",
  "task_updated", "appointment", "sync", "call_data_deleted", "bye", "error", "recovery", "ring", "ring_ended",
  "operator"];

function connectEvents() {
  const source = new EventSource("/dashboard/api/events");
  let failures = 0;
  source.addEventListener("open", () => {
    failures = 0;
    live.lastSeq = 0;           // the server replays the call in progress from the start
    resetLive();
    if (currentTab === "live") renderLive();
  });
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      try { handleEvent(JSON.parse(message.data)); } catch { /* ignore a malformed event */ }
    });
  }
  source.addEventListener("error", async () => {
    failures += 1;
    if (failures >= 3) {
      try {
        const status = await (await fetch("/dashboard/api/auth", { credentials: "same-origin" })).json();
        if (!status.logged_in) location.replace("/dashboard/login");
      } catch { /* server down: EventSource keeps retrying */ }
    }
  });
}

// ------------------------------------------------------------------ appointments
const appt = { scope: "day", day: null, rows: [] };

function apptQuery() {
  const params = new URLSearchParams({ scope: appt.scope });
  if (appt.scope === "day") params.set("day", appt.day);
  for (const [key, id] of [["branch_id", "appt-branch"], ["doctor_id", "appt-doctor"], ["status", "appt-status"]]) {
    if ($(id).value) params.set(key, $(id).value);
  }
  const q = $("appt-search").value.trim();
  if (q) params.set("q", q);
  return params.toString();
}

async function loadAppointments() {
  appt.day = appt.day || clinicToday();
  $("appt-day").value = appt.day;
  $("appt-day-controls").hidden = appt.scope !== "day";
  document.querySelectorAll("#appt-scope button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.scope === appt.scope)));
  const tbody = $("appt-rows");
  try {
    appt.rows = (await api(`/dashboard/api/appointments?${apptQuery()}`)).appointments;
  } catch (err) {
    tbody.replaceChildren(emptyRow(9, err.message));
    return;
  }
  renderAppointments();
}
const reloadAppointmentsSoon = debounce(loadAppointments, 400);

function renderAppointments() {
  const tbody = $("appt-rows");
  const group = $("appt-group").value;
  const rows = [...appt.rows];
  if (group) rows.sort((a, b) => a[group].localeCompare(b[group]) || a.start.localeCompare(b.start));
  const active = rows.filter((r) => r.status === "booked" || r.status === "needs_reschedule").length;
  const where = appt.scope === "day" ? prettyDay(appt.day) : { upcoming: "Upcoming", past: "Past", all: "All" }[appt.scope];
  $("appt-summary").textContent = `${where} · ${rows.length} shown, ${active} active`;
  if (!rows.length) {
    tbody.replaceChildren(emptyRow(9, "No appointments match."));
    return;
  }
  const out = [];
  let lastGroup = null;
  const now = new Date();
  for (const r of rows) {
    if (group && r[group] !== lastGroup) {
      lastGroup = r[group];
      out.push(el("tr", { class: "group-row" }, el("td", { colspan: 9, text: lastGroup })));
    }
    const when = appt.scope === "day" ? `${r.time}–${r.end_time}` : fmtDateTime(r.start);
    const changeable = (r.status === "booked" || r.status === "needs_reschedule") && new Date(r.start) > now;
    out.push(el("tr", { class: r.status === "cancelled" ? "muted-row" : "" },
      el("td", { class: "nowrap num", text: when }),
      el("td", {}, r.patient_name, r.patient_age ? el("span", { class: "muted small", text: ` · ${r.patient_age} y` }) : null,
        r.name_unverified ? el("div", {}, el("span", { class: "badge warn", text: "name unverified" })) : null),
      el("td", { class: "nowrap num", text: r.phone_display }),
      el("td", { text: r.service }),
      el("td", { text: r.doctor }),
      el("td", { text: r.branch }),
      el("td", {}, el("span", { class: `badge ${r.status}`, text: STATUS_LABEL[r.status] || r.status }),
        r.cancel_reason ? el("div", { class: "muted small", text: r.cancel_reason }) : null),
      el("td", { title: r.calendar_synced ? "In Google Calendar" : "Waiting to sync", text: r.calendar_synced ? "✓" : "…" }),
      el("td", { class: "actions" }, changeable ? [
        el("button", { class: "btn small", type: "button", text: "Move", onclick: () => openBook("move", r) }),
        el("button", { class: "btn small danger", type: "button", text: "Cancel", onclick: () => openCancel(r) }),
      ] : null)));
  }
  tbody.replaceChildren(...out);
}

document.querySelectorAll("#appt-scope button").forEach((button) => button.addEventListener("click", () => {
  appt.scope = button.dataset.scope;
  loadAppointments();
}));
$("appt-day").addEventListener("change", () => { appt.day = $("appt-day").value || clinicToday(); loadAppointments(); });
$("appt-prev").addEventListener("click", () => { appt.day = shiftDay(appt.day, -1); loadAppointments(); });
$("appt-next").addEventListener("click", () => { appt.day = shiftDay(appt.day, 1); loadAppointments(); });
$("appt-today").addEventListener("click", () => { appt.day = clinicToday(); loadAppointments(); });
$("appt-branch").addEventListener("change", () => { fillDoctorFilter(); loadAppointments(); });
$("appt-doctor").addEventListener("change", loadAppointments);
$("appt-status").addEventListener("change", loadAppointments);
$("appt-group").addEventListener("change", renderAppointments);
$("appt-search").addEventListener("input", debounce(loadAppointments, 300));
$("appt-export").addEventListener("click", () => {
  // A plain navigation: the browser downloads the file with the session cookie.
  location.href = `/dashboard/api/appointments.csv?${apptQuery()}`;
});
$("appt-new").addEventListener("click", () => openBook("book"));

function describe(r) {
  return `${r.patient_name}: ${r.service} with ${r.doctor}, ${fmtDateTime(r.start)} at ${r.branch}`;
}

let cancelTarget = null;
function openCancel(r) {
  cancelTarget = r;
  $("cancel-what").textContent = describe(r);
  $("cancel-reason").value = "";
  showError($("cancel-error"), "");
  $("dlg-cancel").showModal();
}

$("cancel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("cancel-confirm");
  button.disabled = true;
  try {
    await api(`/dashboard/api/appointments/${encodeURIComponent(cancelTarget.id)}/cancel`, {
      method: "POST", body: { reason: $("cancel-reason").value, version: cancelTarget.version, idem_key: newKey() },
    });
    $("dlg-cancel").close();
    toast("Appointment cancelled.");
    loadAppointments();
  } catch (err) {
    showError($("cancel-error"), err.message);
  } finally {
    button.disabled = false;
  }
});

// Book and move share one dialog: pick where and when, then who (book only).
const booking = { mode: "book", target: null, slot: null, key: null };

function doctorsFor(serviceId, branchId) {
  return catalog.doctors.filter((d) => d.services.includes(serviceId) && (!branchId || d.branch_id === branchId));
}

function fillBookBranches() {
  const serviceId = Number($("book-service").value);
  const branchIds = new Set(doctorsFor(serviceId).map((d) => d.branch_id));
  const select = $("book-branch");
  const current = select.value;
  select.replaceChildren(el("option", { value: "", text: "Any branch" }),
    ...catalog.branches.filter((b) => branchIds.has(b.id)).map((b) => el("option", { value: b.id, text: b.name })));
  select.value = [...select.options].some((o) => o.value === current) ? current : "";
  fillBookDoctors();
}

function fillBookDoctors() {
  const serviceId = Number($("book-service").value);
  const branchId = Number($("book-branch").value) || null;
  const select = $("book-doctor");
  const current = select.value;
  select.replaceChildren(el("option", { value: "", text: "Any doctor" }),
    ...doctorsFor(serviceId, branchId).map((d) => el("option", { value: d.id, text: branchId ? d.name : `${d.name} (${d.branch})` })));
  select.value = [...select.options].some((o) => o.value === current) ? current : "";
  clearSlots();
}

function clearSlots() {
  booking.slot = null;
  $("book-slots").replaceChildren();
  updateBookButton();
}

function updateBookButton() {
  const needsPatient = booking.mode === "book";
  const ready = booking.slot && (!needsPatient || ($("book-name").value.trim() && $("book-phone").value.trim()));
  $("book-confirm").disabled = !ready;
}

function openBook(mode, target = null) {
  booking.mode = mode;
  booking.target = target;
  booking.key = newKey();
  const moving = mode === "move";
  $("dlg-book-title").textContent = moving ? "Move appointment" : "New appointment";
  $("book-confirm").textContent = moving ? "Move it" : "Book";
  $("book-what").hidden = !moving;
  $("book-what").textContent = moving ? describe(target) : "";
  $("book-service-field").hidden = moving;
  $("book-patient").hidden = moving;
  const serviceSelect = $("book-service");
  serviceSelect.replaceChildren(...catalog.services.map((s) => el("option", { value: s.id, text: `${s.name} (${s.duration_min} min)` })));
  serviceSelect.value = moving ? String(target.service_id) : String(catalog.services[0]?.id ?? "");
  $("book-branch").value = "";
  $("book-doctor").value = "";
  fillBookBranches();
  if (moving) {
    $("book-branch").value = String(target.branch_id);
    fillBookDoctors();
    $("book-doctor").value = String(target.doctor_id);
    $("book-day").value = target.date;
  } else {
    $("book-day").value = appt.scope === "day" && appt.day >= clinicToday() ? appt.day : clinicToday();
    for (const id of ["book-name", "book-phone", "book-age"]) $(id).value = "";
  }
  showError($("book-error"), "");
  clearSlots();
  $("dlg-book").showModal();
}

async function findSlots() {
  showError($("book-error"), "");
  const params = new URLSearchParams({ service_id: $("book-service").value, day: $("book-day").value });
  if ($("book-branch").value) params.set("branch_id", $("book-branch").value);
  if ($("book-doctor").value) params.set("doctor_id", $("book-doctor").value);
  if (booking.mode === "move") params.set("ignore_appointment", booking.target.id);
  const box = $("book-slots");
  box.replaceChildren(el("span", { class: "muted small", text: "Looking…" }));
  booking.slot = null;
  updateBookButton();
  try {
    const { slots } = await api(`/dashboard/api/slots?${params}`);
    if (!slots.length) {
      box.replaceChildren(el("span", { class: "muted small", text: "No free times that day. Try another day or doctor." }));
      return;
    }
    const anyDoctor = !$("book-doctor").value;
    box.replaceChildren(...slots.map((slot) => {
      const button = el("button", {
        class: "btn small", type: "button", "aria-pressed": "false",
        text: anyDoctor ? `${slot.time} · ${slot.doctor}` : slot.time,
        title: `${slot.doctor}, ${slot.branch}`,
      });
      button.addEventListener("click", () => {
        box.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
        button.setAttribute("aria-pressed", "true");
        booking.slot = slot;
        updateBookButton();
      });
      return button;
    }));
  } catch (err) {
    box.replaceChildren();
    showError($("book-error"), err.message);
  }
}

$("book-service").addEventListener("change", fillBookBranches);
$("book-branch").addEventListener("change", fillBookDoctors);
$("book-doctor").addEventListener("change", clearSlots);
$("book-day").addEventListener("change", clearSlots);
$("book-find").addEventListener("click", findSlots);
$("book-name").addEventListener("input", updateBookButton);
$("book-phone").addEventListener("input", updateBookButton);

$("book-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!booking.slot) return;
  const button = $("book-confirm");
  button.disabled = true;
  try {
    if (booking.mode === "move") {
      await api(`/dashboard/api/appointments/${encodeURIComponent(booking.target.id)}/reschedule`, {
        method: "POST",
        body: { doctor_id: booking.slot.doctor_id, start: booking.slot.start, version: booking.target.version, idem_key: booking.key },
      });
      toast("Appointment moved.");
    } else {
      await api("/dashboard/api/appointments", {
        method: "POST",
        body: {
          service_id: Number($("book-service").value), doctor_id: booking.slot.doctor_id, start: booking.slot.start,
          patient_name: $("book-name").value.trim(), phone: $("book-phone").value.trim(),
          patient_age: $("book-age").value || null, idem_key: booking.key,
        },
      });
      toast("Appointment booked.");
    }
    $("dlg-book").close();
    if (currentTab === "appointments") loadAppointments();
  } catch (err) {
    showError($("book-error"), err.message);
    if (err.status === 409) booking.key = newKey();   // a fresh attempt, not a replay of the failed one
    updateBookButton();
  }
});

// ------------------------------------------------------------------ tasks
let taskStatus = "open";

async function loadTasks() {
  document.querySelectorAll("#task-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.status === taskStatus)));
  const tbody = $("task-rows");
  let list;
  try {
    list = (await api(`/dashboard/api/tasks?status=${taskStatus}`)).tasks;
  } catch (err) {
    tbody.replaceChildren(emptyRow(8, err.message));
    return;
  }
  if (!list.length) {
    tbody.replaceChildren(emptyRow(8, taskStatus === "open" ? "Nothing waiting. Nice." : "No tasks."));
    return;
  }
  tbody.replaceChildren(...list.map((t) => {
    const box = el("input", { type: "checkbox", "aria-label": `Mark task ${t.id} done` });
    box.checked = t.status === "done";
    box.addEventListener("change", async () => {
      box.disabled = true;
      try {
        await api(`/dashboard/api/tasks/${t.id}`, { method: "POST", body: { done: box.checked } });
        loadTasks();
        refreshSoon();
      } catch (err) {
        toast(err.message, "error");
        box.checked = !box.checked;
        box.disabled = false;
      }
    });
    return el("tr", { class: t.status === "done" ? "muted-row" : "" },
      el("td", {}, box),
      el("td", {}, el("span", { class: `badge ${t.priority}`, text: t.priority })),
      el("td", { class: "nowrap", text: KIND_LABEL[t.kind] || t.kind }),
      el("td", { text: t.note || "–" }),
      el("td", { class: "nowrap num", text: t.phone_display || "–" }),
      el("td", { class: "nowrap", text: fmtDateTime(t.created_at) }),
      el("td", { class: "nowrap", text: t.due_at ? fmtDateTime(t.due_at) : "–" }),
      el("td", {}, t.call_id ? el("button", {
        class: "btn small ghost mono", type: "button", text: t.call_id,
        onclick: () => { showTab("calls"); openCall(t.call_id); },
      }) : "–"));
  }));
}

document.querySelectorAll("#task-filter button").forEach((button) => button.addEventListener("click", () => {
  taskStatus = button.dataset.status;
  loadTasks();
}));

// ------------------------------------------------------------------ calls
let selectedCall = null;

async function loadCalls() {
  const tbody = $("call-rows");
  let list;
  try {
    list = (await api("/dashboard/api/calls?limit=100")).calls;
  } catch (err) {
    tbody.replaceChildren(emptyRow(5, err.message));
    return;
  }
  if (!list.length) {
    tbody.replaceChildren(emptyRow(5, "No calls yet."));
    return;
  }
  tbody.replaceChildren(...list.map((c) => {
    const row = el("tr", { tabindex: "0", "data-call": c.id, class: c.id === selectedCall ? "selected" : "" },
      el("td", { class: "nowrap", text: fmtDateTime(c.started_at) }),
      el("td", { class: "nowrap num", text: c.ended_at ? length(c.started_at, c.ended_at) : "live" }),
      el("td", {}, el("span", { class: "badge", text: c.outcome || "in progress" })),
      el("td", { class: "nowrap num", text: c.phone_display || "–" }),
      el("td", { class: "num", text: c.turns === c.kept_turns ? String(c.turns) : `${c.kept_turns}/${c.turns} kept` }));
    row.addEventListener("click", () => openCall(c.id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openCall(c.id); });
    return row;
  }));
  if (selectedCall) openCall(selectedCall);
}

async function openCall(callId) {
  selectedCall = callId;
  const box = $("call-detail");
  let call;
  try {
    call = await api(`/dashboard/api/calls/${encodeURIComponent(callId)}`);
  } catch (err) {
    box.replaceChildren(el("div", { class: "error-box", text: err.message }));
    return;
  }
  const kept = call.recording_consent !== 0;
  const turns = call.turns.map((t) => {
    const removed = t.text === null;
    const perceived = t.latency && t.latency.perceived_ms;
    const meta = [t.tier !== null ? `tier ${t.tier}` : null, perceived ? `${ms(perceived)} ms` : null,
      t.state_before || t.state_after ? `${t.state_before ?? "?"} → ${t.state_after ?? "?"}` : null].filter(Boolean).join(" · ");
    return el("div", { class: `line ${t.role}${removed ? " removed" : ""}` },
      el("span", { class: "who", text: ROLE_LABEL[t.role] || t.role }),
      el("div", { class: "bubble", text: removed ? "(text removed)" : t.text }),
      meta ? el("span", { class: "meta", text: meta }) : null);
  });
  const deleteButton = el("button", { class: "btn small danger", type: "button", text: "Delete call data" });
  deleteButton.addEventListener("click", async () => {
    const ok = await confirmAction("Delete call data?",
      "This blanks the transcript and the caller's number for this call. Appointments made on the call stay. It can't be undone.",
      "Delete data");
    if (!ok) return;
    try {
      await api(`/dashboard/api/calls/${encodeURIComponent(callId)}/delete-data`, { method: "POST", body: {} });
      toast("Call data deleted.");
      loadCalls();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  box.replaceChildren(
    el("div", { class: "card-head" }, el("h3", {}, "Call ", el("span", { class: "mono", text: call.id })), deleteButton),
    el("dl", { class: "kv" },
      el("dt", { text: "Started" }), el("dd", { text: fmtDateTime(call.started_at) }),
      el("dt", { text: "Length" }), el("dd", { text: call.ended_at ? length(call.started_at, call.ended_at) : "still live" }),
      el("dt", { text: "Direction" }), el("dd", { text: call.direction }),
      el("dt", { text: "Outcome" }), el("dd", { text: call.outcome || "–" }),
      el("dt", { text: "Caller" }), el("dd", { class: "num", text: call.phone_display || "–" }),
      el("dt", { text: "Transcript" }), el("dd", { text: kept ? `kept until ${fmtDateTime(call.purge_after)}` : "not kept (caller's request or deleted)" }),
      call.appointments.length ? [el("dt", { text: "Appointments" }), el("dd", {},
        ...call.appointments.map((a) => el("div", { text: `${a.service}, ${fmtDateTime(a.start_utc)}, ${a.branch} (${STATUS_LABEL[a.status] || a.status})` })))] : null,
      call.tasks.length ? [el("dt", { text: "Tasks" }), el("dd", {},
        ...call.tasks.map((t) => el("div", { text: `${KIND_LABEL[t.kind] || t.kind} · ${t.priority} · ${t.status}` })))] : null),
    el("h3", { class: "small muted", text: "Transcript" }),
    el("div", { class: "transcript" }, ...(turns.length ? turns : [el("div", { class: "empty", text: "No turns recorded." })])));
  document.querySelectorAll("#call-rows tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.call === callId));
}

// ------------------------------------------------------------------ system
async function loadSystem() {
  let data;
  try {
    data = await api("/dashboard/api/system");
  } catch (err) {
    $("sync-detail").textContent = err.message;
    return;
  }
  const cal = data.calendar;
  const state = $("sync-state");
  state.className = `badge ${cal.enabled ? "ok" : "warn"}`;
  state.textContent = cal.enabled ? "On" : "Off";
  const branches = Object.entries(data.calendars).map(([name, ok]) => `${name} ${ok ? "✓" : "✗"}`).join(", ");
  const bits = [cal.enabled ? "Mirroring appointments to Google Calendar." : `Off: ${cal.reason}.`,
    `Branch calendars: ${branches || "none"}.`,
    `${data.outbox_counts.pending} waiting, ${data.outbox_counts.failed} failed.`];
  if (cal.last_ok) bits.push(`Last sync ${fmtDateTime(cal.last_ok)}.`);
  if (cal.last_error) bits.push(`Last error: ${cal.last_error}`);
  $("sync-detail").textContent = bits.join(" ");
  $("sync-retry-all").disabled = !data.outbox_counts.failed;

  const rows = data.outbox.map((o) => el("tr", {},
    el("td", { class: "mono", title: o.appointment_id, text: o.appointment_id.slice(0, 8) }),
    el("td", { class: "nowrap", text: o.start_utc ? fmtDateTime(o.start_utc) : "–" }),
    el("td", { text: o.branch || "–" }),
    el("td", {}, el("span", { class: `badge ${o.status}`, text: o.status })),
    el("td", { class: "num", text: o.attempts }),
    el("td", { class: "small", text: o.last_error || "–" }),
    el("td", { class: "nowrap", text: fmtDateTime(o.due_at) }),
    el("td", { class: "actions" }, el("button", {
      class: "btn small", type: "button", text: "Retry",
      onclick: async () => {
        try {
          await api(`/dashboard/api/sync/${encodeURIComponent(o.appointment_id)}/retry`, { method: "POST", body: {} });
          toast("Queued for another try.");
          loadSystem();
        } catch (err) { toast(err.message, "error"); }
      },
    }))));
  $("sync-rows").replaceChildren(...(rows.length ? rows : [emptyRow(8, "Everything is in sync.")]));

  const dnc = data.do_not_call.map((d) => el("tr", {},
    el("td", { class: "num", text: d.phone_display }),
    el("td", { class: "muted small nowrap", text: fmtDateTime(d.updated_at) }),
    el("td", { class: "actions" }, el("button", {
      class: "btn small", type: "button", text: "Remove",
      onclick: async () => {
        try {
          await api("/dashboard/api/dnc/remove", { method: "POST", body: { phone: d.phone_e164 } });
          loadSystem();
        } catch (err) { toast(err.message, "error"); }
      },
    }))));
  $("dnc-rows").replaceChildren(...(dnc.length ? dnc : [emptyRow(3, "Nobody on the list.")]));

  $("audit-rows").replaceChildren(...data.audit.map((a) => el("tr", {},
    el("td", { class: "nowrap", text: fmtDateTime(a.ts) }),
    el("td", { text: a.actor }),
    el("td", { text: a.action.replace(/_/g, " ") }),
    el("td", { class: "small", text: `${a.entity} ${a.entity_id.length > 12 ? a.entity_id.slice(0, 8) + "…" : a.entity_id}` }))));

  loadHealth(data);
}
const reloadSystemSoon = debounce(loadSystem, 500);

async function loadHealth(system) {
  let health = null;
  try { health = await (await fetch("/health", { credentials: "same-origin" })).json(); } catch { /* shown below */ }
  const yes = (ok, good = "OK", bad = "Not available") => el("span", { class: `badge ${ok ? "ok" : "bad"}`, text: ok ? good : bad });
  const items = [];
  const add = (label, value) => items.push(el("dt", { text: label }), el("dd", {}, value));
  if (!health) {
    add("Server", yes(false, "", "Unreachable"));
  } else {
    add("Speech to text", yes(health.deepgram, "Deepgram ready", "No key"));
    add("Voice", yes(health.elevenlabs, "ElevenLabs ready", "No key"));
    add("Understanding", yes(health.gemini, "Gemini ready", "Unavailable"));
    add("Prompt cache", yes(health.prompt_cache_ready, "Warm", "Warming up"));
    if (health.database) {
      add("Clinic data", `${health.database.branches} branches, ${health.database.doctors} doctors, ${health.database.upcoming_appointments} upcoming`);
    }
  }
  const retention = system.retention;
  add("Transcripts", `Kept ${retention.transcript_days} days, no audio. Last purge ${retention.last_purge ? fmtDateTime(retention.last_purge) : "not yet"}.`);
  add("Dashboard login", yes(system.auth.configured, system.auth.persistent_sessions ? "On" : "On (logins end on restart)", "Locked"));
  add("Call slot", system.call.busy ? `In use (${system.call.kind})` : "Free");
  add("Live viewers", String(system.events.subscribers));
  $("health").replaceChildren(...items);
  $("calls-retention").textContent = `Transcripts only, no audio. Text is removed after ${retention.transcript_days} days.`;
}

$("sync-retry-all").addEventListener("click", async () => {
  try {
    const { retried } = await api("/dashboard/api/sync/retry-failed", { method: "POST", body: {} });
    toast(`${retried} queued for another try.`);
    loadSystem();
  } catch (err) { toast(err.message, "error"); }
});
$("health-refresh").addEventListener("click", loadSystem);
$("dnc-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/dashboard/api/dnc", { method: "POST", body: { phone: $("dnc-phone").value } });
    $("dnc-phone").value = "";
    loadSystem();
  } catch (err) { toast(err.message, "error"); }
});

// ------------------------------------------------------------------ recovery calls
const recovery = { blockId: null, preview: null, filled: false };
const JOB_LABEL = {
  queued: "Waiting", ringing: "Ringing", in_call: "On the call", done: "Done", failed: "Needs staff", skipped: "Skipped",
};
const OUTCOME_LABEL = {
  rescheduled: "Moved", cancelled: "Cancelled", pending: "On hold for front desk", staff: "Wants a call from staff",
  busy: "Busy, call back", do_not_call: "Asked not to be called", wrong_person: "Wrong person answered",
  suspicious: "Unsure it was genuine", declined: "Declined the call", no_answer: "No answer", stale: "Already changed",
  stopped: "Stopped", block_lifted: "Block lifted", abandoned: "Hung up", hung_up: "Hung up", retry: "Retrying",
  not_connected: "Didn't connect", interrupted: "Interrupted by a restart", gone: "Gone",
};

function localInput(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

function fillRecoveryForm(reasons) {
  if (recovery.filled || !catalog.doctors.length) return;
  recovery.filled = true;
  const doctors = $("block-doctor");
  for (const d of catalog.doctors) doctors.append(el("option", { value: d.id, text: `${d.name} (${d.branch})` }));
  const reasonSelect = $("block-reason");
  for (const r of reasons) reasonSelect.append(el("option", { value: r, text: r[0].toUpperCase() + r.slice(1) }));
  const day = localInput(new Date(Date.now() + 24 * 3600 * 1000)).slice(0, 10);
  $("block-start").value = `${day}T09:00`;
  $("block-end").value = `${day}T21:00`;
}

async function loadRecovery() {
  let data;
  try {
    data = await api("/dashboard/api/recovery");
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  fillRecoveryForm(data.reasons || []);
  renderBlocks(data.blocks || []);
  renderRunner(data.runner);
  renderCampaigns(data.campaigns || []);
  renderWindows(data.windows || []);
  if (recovery.blockId && !(data.blocks || []).some((b) => b.id === recovery.blockId)) {
    recovery.blockId = null;
    renderPreview(null);
  } else if (recovery.blockId) {
    showPreview(recovery.blockId, true);
  }
}
const reloadRecoverySoon = debounce(loadRecovery, 400);

function renderBlocks(blocks) {
  const tbody = $("block-rows");
  if (!blocks.length) {
    tbody.replaceChildren(emptyRow(5, "No doctor is blocked."));
    return;
  }
  tbody.replaceChildren(...blocks.map((b) => el("tr", { class: b.id === recovery.blockId ? "selected" : "" },
    el("td", { text: `${b.doctor} (${b.branch})` }),
    el("td", { class: "nowrap", text: fmtDateTime(b.start) }),
    el("td", { class: "nowrap", text: fmtDateTime(b.end) }),
    el("td", { text: b.reason_category }),
    el("td", { class: "nowrap" },
      el("button", { class: "btn small", type: "button", text: "Preview", onclick: () => showPreview(b.id) }),
      " ",
      el("button", {
        class: "btn small danger", type: "button", text: "Lift",
        onclick: async () => {
          const text = `${b.doctor} becomes bookable again. Calls still waiting for this block stop; appointments already moved stay moved.`;
          if (!await confirmAction("Lift this block?", text, "Lift block")) return;
          try {
            await api(`/dashboard/api/recovery/blocks/${b.id}/lift`, { method: "POST", body: {} });
            loadRecovery();
          } catch (err) { toast(err.message, "error"); }
        },
      })))));
}

async function showPreview(blockId, keepTicks = false) {
  recovery.blockId = blockId;
  try {
    renderPreview(await api(`/dashboard/api/recovery/blocks/${blockId}/preview`), keepTicks);
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderPreview(preview, keepTicks = false) {
  const before = new Set([...document.querySelectorAll("#preview-rows input:checked")].map((b) => b.value));
  recovery.preview = preview;
  const tbody = $("preview-rows");
  if (!preview) {
    $("preview-summary").textContent = "Block a doctor or pick a block to see who is affected.";
    tbody.replaceChildren();
    updateCampaignButton();
    return;
  }
  const b = preview.block;
  const span = `${b.doctor}, ${fmtDateTime(b.start)} to ${fmtDateTime(b.end)}`;
  $("preview-summary").textContent = preview.count
    ? `${span}: ${preview.count} booked appointment(s) on ${preview.groups.length} phone number(s). No calls have been made yet.`
    : `${span}: no booked appointments are affected.`;
  const rows = [];
  for (const g of preview.groups) {
    g.appointments.forEach((a, i) => {
      const box = el("input", { type: "checkbox", value: a.id, "aria-label": `Call about ${a.patient_name}'s appointment` });
      box.checked = keepTicks ? before.has(a.id) : !g.do_not_call;
      box.addEventListener("change", updateCampaignButton);
      rows.push(el("tr", {},
        el("td", {}, box),
        el("td", {}, a.patient_name, g.do_not_call && i === 0 ? el("span", { class: "badge bad", text: "do not call" }) : null),
        el("td", { class: "num nowrap", text: i === 0 ? g.phone_masked : "" }),
        el("td", { class: "nowrap", text: fmtDateTime(a.start) }),
        el("td", { text: a.service })));
    });
  }
  tbody.replaceChildren(...(rows.length ? rows : [emptyRow(5, "Nobody to call.")]));
  updateCampaignButton();
}

function updateCampaignButton() {
  const ticked = document.querySelectorAll("#preview-rows input:checked").length;
  const button = $("campaign-start");
  button.disabled = !ticked;
  button.textContent = ticked ? `Start recovery calls (${ticked})` : "Start recovery calls";
}

$("campaign-start").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#preview-rows input:checked")].map((b) => b.value);
  if (!ids.length || !recovery.blockId) return;
  const text = `Emma will call about ${ids.length} appointment(s), one patient at a time, and offer only valid new times.`;
  if (!await confirmAction("Start recovery calls?", text, "Start calls")) return;
  try {
    await api("/dashboard/api/recovery/campaigns", { method: "POST", body: { block_id: recovery.blockId, appointment_ids: ids } });
    toast("Recovery calls started.");
    loadRecovery();
  } catch (err) { toast(err.message, "error"); }
});

$("block-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError($("block-error"), "");
  try {
    const res = await api("/dashboard/api/recovery/blocks", {
      method: "POST",
      body: {
        doctor_id: $("block-doctor").value, start: $("block-start").value, end: $("block-end").value,
        reason: $("block-reason").value, note: $("block-note").value,
      },
    });
    recovery.blockId = res.block_id;
    $("block-note").value = "";
    renderPreview(res.preview);
    loadRecovery();
  } catch (err) { showError($("block-error"), err.message); }
});

function renderRunner(runner) {
  const badge = $("runner-state");
  const detail = $("runner-detail");
  if (!runner) {
    badge.textContent = "Off";
    badge.className = "badge";
    detail.textContent = "";
  } else if (runner.ringing) {
    badge.textContent = "Ringing";
    badge.className = "badge warn";
    detail.textContent = `Calling ${runner.ringing.to_name || "the patient"} (${runner.ringing.to_masked}). Answer on the patient page.`;
  } else if (runner.paused) {
    badge.textContent = "Paused";
    badge.className = "badge warn";
    detail.textContent = `Waiting: ${runner.paused}.`;
  } else if (runner.waiting && runner.waiting.queued) {
    const w = runner.waiting;
    badge.textContent = "Waiting";
    badge.className = "badge info";
    detail.textContent = `${w.queued} call(s) queued` + (w.next_retry ? `; next try ${fmtDateTime(w.next_retry)}` : "")
      + (w.paused_campaigns ? `; ${w.paused_campaigns} campaign(s) paused` : "")
      + `. Calls ring only ${runner.window} clinic time and inside each patient's own hours.`;
  } else {
    badge.textContent = "Ready";
    badge.className = "badge ok";
    detail.textContent = `Calls are made one at a time, ${runner.window} clinic time, never while another call is on. Unanswered calls are tried again later.`;
  }
}

function jobBadge(status) {
  if (status === "done") return "ok";
  if (status === "failed") return "bad";
  return status === "skipped" ? "" : "warn";
}

function campaignButton(c, action, label, cls, confirmText) {
  return el("button", {
    class: `btn small ${cls}`, type: "button", text: label,
    onclick: async () => {
      if (confirmText && !await confirmAction(`${label} these calls?`, confirmText, `${label} calls`)) return;
      try {
        await api(`/dashboard/api/recovery/campaigns/${c.id}/${action}`, { method: "POST", body: {} });
        loadRecovery();
      } catch (err) { toast(err.message, "error"); }
    },
  });
}

function summaryText(c) {
  const a = (c.summary || {}).appointments || {};
  const k = (c.summary || {}).calls || {};
  const parts = [];
  if (a.moved) parts.push(`${a.moved} moved`);
  if (a.cancelled) parts.push(`${a.cancelled} cancelled`);
  if (a.needs_reschedule) parts.push(`${a.needs_reschedule} need a new time`);
  if (k.retrying) parts.push(`${k.retrying} call${k.retrying === 1 ? "" : "s"} to retry`);
  if (k.waiting) parts.push(`${k.waiting} waiting`);
  if (k.skipped) parts.push(`${k.skipped} skipped`);
  return parts.length ? parts.join(" · ") : "No results yet.";
}

function jobCallLabel(j) {
  if (j.status === "queued" && j.next_attempt_at) {
    return `Retry ${fmtDateTime(j.next_attempt_at)} (try ${j.attempts + 1} of ${Math.max(j.max_attempts, j.attempts + 1)})`;
  }
  return JOB_LABEL[j.status] || j.status;
}

function renderCampaigns(campaigns) {
  const box = $("campaign-list");
  if (!campaigns.length) {
    box.replaceChildren(el("div", { class: "empty", text: "No recovery calls yet." }));
    return;
  }
  box.replaceChildren(...campaigns.map((c) => {
    const running = c.status === "running";
    const paused = running && Boolean(c.paused_at);
    const controls = running ? [
      paused ? campaignButton(c, "resume", "Resume", "primary") : campaignButton(c, "pause", "Pause", ""),
      campaignButton(c, "stop", "Stop", "danger", "Calls still waiting won't be made. A call in progress finishes."),
    ] : [];
    const head = el("div", { class: "card-head" },
      el("h4", { text: `${c.doctor} · started ${fmtDateTime(c.created_at)}` }),
      el("span", {
        class: `badge ${paused ? "warn" : running ? "warn" : c.status === "completed" ? "ok" : ""}`,
        text: paused ? "paused" : c.status,
      }),
      ...controls,
      el("a", { class: "btn small ghost", href: `/dashboard/api/recovery/campaigns/${c.id}.csv`, text: "Export CSV" }));
    const rows = c.jobs.map((j) => el("tr", {},
      el("td", { text: j.name || "–" }),
      el("td", { class: "num nowrap", text: j.phone_masked }),
      el("td", {}, ...j.appointments.map((a) => el("div", { class: "nowrap" },
        `${fmtDateTime(a.start)} · ${a.service} · ${a.doctor} `,
        el("span", { class: `badge ${a.status}`, text: STATUS_LABEL[a.status] || a.status })))),
      el("td", {}, el("span", { class: `badge ${jobBadge(j.status)}`, text: jobCallLabel(j) })),
      el("td", { text: j.outcome ? (OUTCOME_LABEL[j.outcome] || j.outcome) : "–" }),
      el("td", {}, j.call_id && ["done", "failed"].includes(j.status) ? el("button", {
        class: "btn small ghost mono", type: "button", text: j.call_id,
        onclick: () => { showTab("calls"); openCall(j.call_id); },
      }) : "–")));
    const header = ["Patient", "Phone", "Appointments", "Call", "Result", "Transcript"].map((h) => el("th", { text: h }));
    return el("div", { class: "campaign" }, head, el("p", { class: "muted small", text: summaryText(c) }),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ...header)), el("tbody", {}, ...rows))));
  }));
}

function renderWindows(windows) {
  const tbody = $("window-rows");
  if (!windows.length) {
    tbody.replaceChildren(emptyRow(3, "No patient has their own calling hours."));
    return;
  }
  tbody.replaceChildren(...windows.map((w) => el("tr", {},
    el("td", { class: "num nowrap", text: w.phone_masked }),
    el("td", { text: w.call_after || "–" }),
    el("td", { text: w.call_before || "–" }))));
}

$("window-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/dashboard/api/recovery/windows", {
      method: "POST",
      body: { phone: $("window-phone").value, after: $("window-after").value, before: $("window-before").value },
    });
    $("window-phone").value = $("window-after").value = $("window-before").value = "";
    toast("Calling hours saved.");
    loadRecovery();
  } catch (err) { toast(err.message, "error"); }
});

// ------------------------------------------------------------------ start
const refreshSoon = debounce(refreshOverview, 500);

$("logout").addEventListener("click", async () => {
  try { await fetch("/dashboard/api/logout", { method: "POST", credentials: "same-origin" }); } finally {
    location.replace("/dashboard/login");
  }
});

async function start() {
  await refreshOverview();
  try { await loadCatalog(); } catch (err) { toast(err.message, "error"); }
  connectEvents();
  const wanted = location.hash.slice(1) || recall("tab") || "live";
  showTab(wanted);
  setInterval(refreshOverview, 30000);
}

start();
