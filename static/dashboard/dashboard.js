// Pearl Dental staff dashboard. Vanilla JS, no build step, no CDN.
//
// Layout (docs/APPLE_DESIGN_PLAN.md 4.4): a glass sidebar, a Today tile grid, Live call as
// Messages, Appointments as Calendar, Tasks as Reminders, Calls as Mail, System as Settings.
// Every API call and action from the earlier dashboard is kept; only the presentation changed.
//
// Every value from the server goes into the page with textContent, never innerHTML, so a
// caller who gives "<script>" as their name stays plain text. All times are shown in the
// clinic's timezone, whatever the browser's is.

import {
  reveal, replay, cardHover, toast as glassToast, tabnav, sheet, popIn, spinner, accordion,
} from "/static/ui/emma-ui.js";

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

function svgIcon(id, cls = "ico") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
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

function ago(value) {
  if (!value) return "";
  const secs = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (Number.isNaN(secs)) return "";
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d ago`;
  return fmt(value, { day: "numeric", month: "short" });
}

function clinicToday() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

// Minutes since midnight, clinic time, right now.
function clinicNowMinutes() {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date());
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return get("hour") * 60 + get("minute");
}

const isoDate = (y, m, d) => new Date(Date.UTC(y, m, d)).toISOString().slice(0, 10);
function parts(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m: m - 1, d };
}
function shiftDay(iso, days) {
  const { y, m, d } = parts(iso);
  return isoDate(y, m, d + days);
}
function weekday(iso) {                       // 0 = Monday … 6 = Sunday
  const { y, m, d } = parts(iso);
  return (new Date(Date.UTC(y, m, d)).getUTCDay() + 6) % 7;
}
function startOfWeek(iso) { return shiftDay(iso, -weekday(iso)); }
function startOfMonth(iso) { const { y, m } = parts(iso); return isoDate(y, m, 1); }
function shiftMonth(iso, months) {
  const { y, m, d } = parts(iso);
  const last = new Date(Date.UTC(y, m + months + 1, 0)).getUTCDate();
  return isoDate(y, m + months, Math.min(d, last));
}
function dayFmt(iso, options) {
  const { y, m, d } = parts(iso);
  return new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", ...options }).format(new Date(Date.UTC(y, m, d)));
}
const prettyDay = (iso) => dayFmt(iso, { weekday: "long", day: "numeric", month: "long" });

function length(startIso, endIso) {
  if (!startIso) return "–";
  const secs = Math.max(0, Math.round(((endIso ? new Date(endIso) : new Date()) - new Date(startIso)) / 1000));
  const m = Math.floor(secs / 60);
  return m ? `${m}m ${secs % 60}s` : `${secs}s`;
}
function clock(startDate) {
  const secs = Math.max(0, Math.round((Date.now() - startDate.getTime()) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

function ms(value) {
  return value === null || value === undefined ? "–" : Math.round(value).toLocaleString("en-IN");
}

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

const toast = (message, kind = "") => glassToast(message, kind);

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
function emptyItem(text) {
  return el("li", { class: "empty", text });
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
const LIVE_ROLE_LABEL = { caller: "Caller", emma: "Emma", operator: "You" };
// How a reply was made, in front-desk words (shown only inside "Response times").
const TIER_LABEL = { 0: "Quick reply", 1: "Thought it through", 2: "Backup reply" };
// What Emma is doing on the call, by the step she is on. Never shown as the raw key.
const GOAL_LABEL = {
  greet: "Greeting the caller", greeting: "Greeting the caller", greeting_offer: "Offering help", go_on: "Offering help",
  offer_help: "Offering help", ask_intent: "Finding out what they need", ask_name: "Asking for their name",
  spell_name: "Checking how their name is spelt", spelling: "Checking how their name is spelt",
  ask_phone: "Asking for their number", confirm_phone: "Confirming their number", ask_service: "Asking what it's for",
  ask_branch: "Choosing a branch", ask_when: "Finding a day", ask_new_when: "Finding a new day",
  ask_appt_date: "Finding their appointment", ask_time: "Choosing a time", offer: "Offering times",
  offer_slots: "Offering times", offer_new_slots: "Offering new times", offer_rebook: "Offering another time",
  summary: "Reading back the booking", summary_again: "Reading back the booking",
  duplicate: "Checking an existing booking", duplicate_check: "Checking an existing booking",
  pick_appointment: "Finding their appointment", confirm_cancel: "Confirming a cancellation",
  ask_cancel_reason: "Asking why they're cancelling", confirm_change: "Confirming the new time",
  confirm_reschedule: "Confirming the new time", callback: "Arranging a call back", callback_offer: "Offering a call back",
  callback_task: "Arranging a call back", callback_done: "Arranging a call back", ask_age: "Asking the patient's age",
  ask_patient: "Asking who the patient is", verify_failed: "Checking who is calling", end: "Saying goodbye",
  done: "Saying goodbye", confirmed: "Wrapping up",
};
function goalText(goal) {
  if (!goal) return "";
  const key = String(goal).replace(/^step\s+/, "");
  return GOAL_LABEL[key] || "On a call";
}
// Where an appointment came from.
const SOURCE_LABEL = { call: "Emma, on a call", inbound: "Emma, on a call", outbound: "Emma, on a recovery call",
  dashboard: "The front desk", seed: "Set up by the clinic" };
const CHANNEL_LABEL = { browser: "Browser", phone: "Phone" };
// Where a caller hung up, by the step Emma was on.
const STEP_LABEL = {
  greet: "at the greeting", go_on: "after the greeting", ask_name: "while giving their name",
  spell_name: "while spelling their name", confirm_phone: "while confirming their number",
  ask_phone: "while giving their number", ask_service: "while saying what it's for", ask_branch: "choosing a branch",
  ask_when: "choosing a day", ask_time: "choosing a time", offer_slots: "choosing a slot", summary: "at the summary",
  duplicate_check: "about an existing appointment", callback_offer: "after a callback offer",
  ask_intent: "while saying what they needed", greeting: "at the greeting", greeting_offer: "after the greeting",
  offer_help: "after the greeting", spelling: "while spelling their name", ask_new_when: "choosing a new day",
  ask_appt_date: "while finding their appointment", offer: "choosing a time", offer_new_slots: "choosing a new time",
  offer_rebook: "choosing another time", summary_again: "at the summary", duplicate: "about an existing appointment",
  pick_appointment: "while finding their appointment", confirm_cancel: "while confirming a cancellation",
  ask_cancel_reason: "while giving a reason to cancel", confirm_change: "while confirming the new time",
  confirm_reschedule: "while confirming the new time", callback: "while arranging a call back",
  callback_task: "while arranging a call back", ask_age: "while giving the patient's age",
  ask_patient: "while saying who the patient is", verify_failed: "while we checked who was calling",
  end: "at the goodbye", done: "at the goodbye",
};
const CALL_OUTCOME = {
  booked: "Booked", rescheduled: "Moved", cancelled: "Cancelled", completed: "Completed", transferred: "Transferred",
  booked_hangup: "Booked, hung up before hearing it", rescheduled_hangup: "Moved, hung up before hearing it",
  cancelled_hangup: "Cancelled, hung up before hearing it", ended_by_staff: "Ended by staff", ended: "Ended",
  abandoned: "Hung up", hung_up: "Hung up", emergency: "Emergency", red_flag: "Red flag", callback: "Call back arranged",
  language: "Needed another language", escalation: "Passed to staff", not_connected: "Didn't connect",
  no_answer: "No answer", declined: "Declined",
};

function callOutcome(outcome) {
  if (!outcome) return "In progress";
  if (CALL_OUTCOME[outcome]) return CALL_OUTCOME[outcome];
  const [kind, step] = outcome.split("@");
  if (kind === "abandoned" || kind === "hung_up") return `Hung up ${STEP_LABEL[step] || "during the call"}`;
  if (CALL_OUTCOME[kind]) return CALL_OUTCOME[kind];
  return "Call ended";
}
// The outcome's dot: green for a booking change, red only when something is urgent.
function outcomeTone(outcome) {
  const kind = (outcome || "").split("@")[0];
  if (!outcome) return "live";
  if (/^(booked|rescheduled)/.test(kind)) return "good";
  if (/^(emergency|red_flag)/.test(kind)) return "urgent";
  if (/^(ended_by_staff|transferred|escalation|callback)/.test(kind)) return "staff";
  return "plain";
}

// ------------------------------------------------------------------ sheets (B6)
// Each sheet rises 16px and fades in over the blurred light scrim; on a phone it is a bottom
// sheet with a grab handle. The close button, the scrim, Escape and [data-sheet-close] close it,
// and focus goes back to whatever opened it.
const sheetClosed = {};
const sheetOpener = {};
const pageBehind = () => [document.querySelector(".app"), $("demo-ribbon")];
function syncSheetLock() {
  // One sheet can close while the next opens (inspector → Move): keep the page behind locked.
  const anyOpen = Boolean(document.querySelector(".e-sheet-root.is-open"));
  document.documentElement.classList.toggle("e-sheet-open", anyOpen);
  // The page behind an open sheet is inert: not focusable, not in the accessibility tree.
  for (const node of pageBehind()) if (node) node.inert = anyOpen;
}
function makeSheet(id) {
  const ctl = sheet($(id), {
    onClose: () => {
      syncSheetLock();
      // The shared sheet tries to focus the opener while the page is still inert; do it again now.
      const opener = sheetOpener[id];
      if (opener && opener.isConnected && !document.querySelector(".e-sheet-root.is-open")) opener.focus({ preventScroll: true });
      setTimeout(syncSheetLock, reducedMotion() ? 220 : 620);
      const done = sheetClosed[id];
      sheetClosed[id] = null;
      if (done) done();
    },
  });
  return {
    open(onClose) {
      sheetClosed[id] = onClose || null;
      const active = document.activeElement;
      if (active && active !== document.body && !active.closest(".e-sheet-root")) sheetOpener[id] = active;
      ctl.open();
      syncSheetLock();
    },
    close: ctl.close,
    get isOpen() { return $(id).classList.contains("is-open"); },
  };
}
const sheets = {
  inspector: makeSheet("appt-popover"),
  cancel: makeSheet("dlg-cancel"),
  book: makeSheet("dlg-book"),
  confirm: makeSheet("dlg-confirm"),
};

let confirmAnswer = false;
function confirmAction(title, text, okLabel = "Confirm") {
  $("dlg-confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = okLabel;
  confirmAnswer = false;
  return new Promise((resolve) => sheets.confirm.open(() => resolve(confirmAnswer)));
}
$("confirm-form").addEventListener("submit", (event) => {
  event.preventDefault();
  confirmAnswer = true;
  sheets.confirm.close();
});

// B14: the 8-spoke spinner beside a short line, for anything still loading.
function loadingNode(text = "Loading…", tag = "div") {
  return el(tag, { class: "loading", role: "status" }, spinner(16), el("span", { text }));
}

function showError(box, message) {
  box.textContent = message || "";
  box.hidden = !message;
}

// ------------------------------------------------------------------ catalogue and doctor colours
const catalog = { services: [], doctors: [], branches: [] };
let dayHours = [8, 21];

function doctorColor(doctorId) {
  const i = catalog.doctors.findIndex((d) => d.id === doctorId);
  return `var(--doc-${(i < 0 ? 0 : i % 8) + 1})`;
}

async function loadCatalog() {
  Object.assign(catalog, await api("/dashboard/api/catalog"));
  const branchSelect = $("appt-branch");
  for (const b of catalog.branches) branchSelect.append(el("option", { value: b.id, text: b.name }));
  fillDoctorFilter();
  renderDoctorToggles();
  try {
    const data = await api("/dashboard/api/doctors");
    const [from, to] = (data.day_hours || []).map((t) => Number(String(t).split(":")[0]));
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) dayHours = [from, to];
  } catch { /* keep the default hours */ }
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

// ------------------------------------------------------------------ overview: sidebar counts, alerts
let overview = null;

async function refreshOverview() {
  let data;
  try { data = await api("/dashboard/api/overview"); } catch { return; }
  overview = data;
  if (data.timezone) TZ = data.timezone;
  const openTasks = Object.values(data.tasks).reduce((a, b) => a + b, 0);
  const urgent = data.tasks.urgent || 0;
  const count = $("tab-task-count");
  count.textContent = urgent ? `${openTasks} · ${urgent}!` : String(openTasks);
  count.hidden = openTasks === 0;
  const badge = $("tab-task-badge");
  badge.textContent = String(openTasks);
  badge.hidden = openTasks === 0;
  const failed = data.sync.failed || 0;
  const chipSync = $("chip-sync");
  chipSync.hidden = failed === 0;
  chipSync.textContent = String(failed);
  chipSync.title = `${failed} calendar sync${failed === 1 ? "" : "s"} failed`;
  updateCallChip(data.call);
  renderAlerts(data.alerts || []);
  $("today-date").textContent = prettyDay(clinicToday());
  if (currentTab === "today") renderTodaySoon();
}

// Problems the server found (alerts.py): a speech service down, calendar sync stuck, a call line stuck.
// B21: a new notice drops in from the top edge in its accent colour, then calms to a tint.
// One already shown stays calm when the overview refreshes.
const seenAlerts = new Set();
function renderAlerts(alerts) {
  requestAnimationFrame(fitCalendar);
  const box = $("alerts");
  box.hidden = alerts.length === 0;
  box.replaceChildren(...alerts.map((a) => {
    const key = `${a.level}|${a.text}|${a.since}`;
    const fresh = !seenAlerts.has(key);
    seenAlerts.add(key);
    return el("p", { class: `alert-line ribbon ${a.level}${fresh ? "" : " calm"}`,
      text: `${a.text} (since ${fmtDateTime(a.since)})` });
  }));
}

let callBusy = false;
function updateCallChip(call) {
  callBusy = Boolean(call && call.busy);
  $("side-live").hidden = !callBusy;
  $("tab-live-dot").hidden = !callBusy;
  renderSideTimer();
}
function renderSideTimer() {
  const running = [...liveCalls.values()].find((st) => !st.ended && st.started);
  $("side-live-time").textContent = running ? clock(running.started) : "";
}

// ------------------------------------------------------------------ sections (sidebar, tab bar, hash)
const loaders = {
  today: () => renderToday(),
  live: () => renderLive(),
  appointments: () => loadAppointments(),
  tasks: () => loadTasks(),
  calls: () => loadCalls(),
  recovery: () => loadRecovery(),
  system: () => loadSystem(),
};
const TAB_FOR = { calls: "more", recovery: "more", system: "more" };
let currentTab = "today";

function showTab(name) {
  if (!loaders[name]) name = "today";
  const changed = name !== currentTab;
  currentTab = name;
  closePopover();
  $("more-sheet").hidden = true;
  document.querySelectorAll(".side-item[data-tab]").forEach((item) => item.setAttribute("aria-selected", String(item.dataset.tab === name)));
  document.querySelectorAll(".tab-item").forEach((item) => item.setAttribute("aria-selected", String(item.dataset.tab === (TAB_FOR[name] || name))));
  for (const key of Object.keys(loaders)) $(`view-${key}`).hidden = key !== name;
  $("side-calendar").hidden = name !== "appointments";
  store("tab", name);
  // A section that was hidden lays its segmented controls out from nothing: place the
  // indicator without sliding it in from the left (apple.com's first-layout rule).
  if (changed) settleTabnavs();
  if (location.hash !== `#${name}`) history.pushState(null, "", `#${name}`);
  document.title = `${{ today: "Today", live: "Live call", appointments: "Appointments", tasks: "Tasks", calls: "Calls",
    recovery: "Recovery", system: "System" }[name]} · ${document.title.split(" · ").slice(-1)[0]}`;
  if (changed) window.scrollTo({ top: 0 });
  if (changed && name === "system") $("view-system").classList.remove("drilled");   // Settings opens on its list
  if (changed && name === "calls") $("view-calls").classList.remove("drilled");     // and Mail on its list
  updateBars();
  loaders[name]();
  requestAnimationFrame(fitCalendar);
  if (name === "today" && changed) replay($("today-grid"));
}

document.querySelectorAll(".side-item[data-tab]").forEach((item) => item.addEventListener("click", () => showTab(item.dataset.tab)));
document.querySelectorAll(".tab-item").forEach((item) => item.addEventListener("click", () => {
  if (item.dataset.tab === "more") {
    $("more-sheet").hidden = !$("more-sheet").hidden;
    return;
  }
  showTab(item.dataset.tab);
}));
document.addEventListener("click", (event) => {
  const go = event.target.closest("[data-go]");
  if (go) {
    if (go.tagName === "A") event.preventDefault();
    showTab(go.dataset.go);
  }
  const sheet = $("more-sheet");
  if (!sheet.hidden && !event.target.closest("#more-sheet") && !event.target.closest("#tab-more")) sheet.hidden = true;
});
window.addEventListener("popstate", () => showTab(location.hash.slice(1)));

function settleTabnavs() {
  const navs = [...document.querySelectorAll(".e-tabnav")];
  navs.forEach((nav) => nav.classList.add("e-no-anim"));
  requestAnimationFrame(() => {
    segmentPlacers.forEach((place) => place());
    requestAnimationFrame(() => navs.forEach((nav) => nav.classList.remove("e-no-anim")));
  });
}

// B15, iOS navigation bar: the section toolbar is clear while it sits in place and turns to
// glass (with a 1px keyline) only once it is stuck, with content scrolling under it; the small
// title appears once the large title has gone under the bar.
let barQueued = false;
function updateBars() {
  barQueued = false;
  const view = document.querySelector(".view:not([hidden])");
  if (!view) return;
  const bar = view.querySelector(".view-bar");
  const top = view.getBoundingClientRect().top;      // the bar's own place, before it sticks
  bar.classList.toggle("stuck", top < -1);
  bar.classList.toggle("titled", top < -40);
}
window.addEventListener("scroll", () => {
  if (!barQueued) { barQueued = true; requestAnimationFrame(updateBars); }
}, { passive: true });

// ------------------------------------------------------------------ live call
// Up to MAX_CONCURRENT_CALLS calls can be on at once (11 Oct): each has its own state, and the
// Live view follows one of them (`live`); the chips above the transcript switch between them.
function newLiveState(callId = null) {
  return {
    callId, started: callId ? new Date() : null, ended: false, state: callId ? "listening" : "idle", lines: [],
    interim: "", turns: new Map(), goal: null, tier: null, entities: {}, outcome: null, operator: false,
    staffSaid: [], ending: false, endedAt: null, endedByMe: false,
  };
}
const liveCalls = new Map();          // call_id -> state
let live = newLiveState();
let lastSeq = 0;

function resetLive() {
  liveCalls.clear();
  live = newLiveState();
}

function followCall(callId) {
  const st = liveCalls.get(callId);
  if (!st) return;
  live = st;
  renderLive();
}

function stateFor(event) {
  const id = event.call_id;
  if (!id) return null;
  let st = liveCalls.get(id);
  if (!st) {
    st = newLiveState(id);
    liveCalls.set(id, st);
  }
  // Follow a new call when the one shown has ended (or nothing is shown yet).
  if (!live.callId || (live.ended && st !== live && !st.ended)) live = st;
  return st;
}

function addCaption(st, who, text, final, turn) {
  if (!text) return;
  if (who === "emma") {
    // Each of Emma's turns is one line, growing sentence by sentence as the caller hears it.
    // A line staff typed during a takeover is spoken in Emma's voice but is staff's: it shows
    // as a green "You" bubble (matched on the text staff sent).
    const same = turn !== undefined && turn !== null
      ? st.lines.find((line) => (line.who === "emma" || line.who === "operator") && line.turn === turn) : null;
    const typedByStaff = isStaffLine(st, text);
    if (same) {
      same.text = text;
      if (typedByStaff) same.who = "operator";
      return;
    }
    const last = st.lines[st.lines.length - 1];
    if (last) last.closed = true;
    st.lines.push({ who: typedByStaff ? "operator" : "emma", text, closed: true, turn });
    return;
  }
  if (!final) {
    st.interim = text;
    return;
  }
  st.interim = "";
  const last = st.lines[st.lines.length - 1];
  if (last && last.who === "caller" && !last.closed) last.text = `${last.text} ${text}`;
  else st.lines.push({ who: "caller", text, closed: false });
}

const norm = (s) => String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
function isStaffLine(st, text) {
  const t = norm(text);
  return Boolean(t) && (st.staffSaid || []).some((said) => said === t || said.startsWith(t) || t.startsWith(said));
}

// The caller's turn as Emma took it (the stored transcript). On the phone a turn often ends
// before speech-to-text sends a final caption, so the caption alone left only a passing
// "hearing…" line and the caller's words vanished from the conversation (owner's call, 14 Oct).
function commitCallerTurn(st, text) {
  st.interim = "";
  const last = st.lines[st.lines.length - 1];
  if (last && last.who === "caller") {
    last.text = text;                 // the words Emma acted on replace the live caption
    last.closed = true;
    return;
  }
  st.lines.push({ who: "caller", text, closed: true });
}

function handleEvent(event) {
  if (event.seq && event.seq <= lastSeq) return;
  if (event.seq) lastSeq = event.seq;
  const CALL_EVENTS = ["call_started", "caption", "state", "turn", "metrics", "call_turn", "call_ended", "operator"];
  const st = CALL_EVENTS.includes(event.type) ? stateFor(event) : null;
  if (CALL_EVENTS.includes(event.type) && !st) return;
  switch (event.type) {
    case "call_started":
      Object.assign(st, newLiveState(event.call_id));
      updateCallChip({ busy: true });
      break;
    case "caption":
      addCaption(st, event.who === "emma" ? "emma" : "caller", event.text, event.final, event.turn);
      break;
    case "state":
      if (!st.ended && !st.ending) st.state = event.state;
      break;
    case "turn":
      if (event.phase === "start" && !st.ended && !st.ending) st.state = "speaking";
      break;
    case "metrics":
      st.turns.set(event.turn, event);
      if (event.tier !== undefined && event.tier !== null) st.tier = event.tier;
      if (event.goal_after || event.step_after) st.goal = event.goal_after || `step ${event.step_after}`;
      break;
    case "call_turn": {
      const meta = event.meta || {};
      if (event.role === "caller" && event.text) commitCallerTurn(st, event.text);
      if (event.role === "operator" && event.text) {
        // The stored transcript says this line was staff's: make sure it shows that way.
        const t = norm(event.text);
        const line = [...st.lines].reverse().find((l) => l.who === "emma" && norm(l.text) === t);
        if (line) line.who = "operator";
      }
      if (meta.goal_after) st.goal = meta.goal_after;
      if (meta.tier !== undefined && meta.tier !== null) st.tier = meta.tier;
      if (meta.entities && typeof meta.entities === "object") Object.assign(st.entities, meta.entities);
      if (meta.action) {
        // Emma just booked, moved or cancelled something: the lists change too.
        st.outcome = meta.action;
        refreshSoon();
        if (currentTab === "appointments") reloadAppointmentsSoon();
      }
      break;
    }
    case "call_ended":
      st.ended = true;
      st.state = "ended";
      st.operator = false;
      st.endedAt = new Date();
      st.outcome = event.outcome;
      // Ended calls drop off the switcher once another call is shown; the one shown stays to read.
      for (const [id, other] of liveCalls) if (other.ended && other !== live && other !== st) liveCalls.delete(id);
      if (st === live) {
        const next = [...liveCalls.values()].find((other) => !other.ended);
        if (next) live = next;
      }
      updateCallChip({ busy: [...liveCalls.values()].some((other) => !other.ended) });
      refreshSoon();
      if (currentTab === "calls") loadCalls();
      if (currentTab === "appointments") reloadAppointmentsSoon();
      break;
    case "task_created":
      toast(`New task: ${KIND_LABEL[event.kind] || "Call back"}${event.priority === "normal" ? "" : ` (${event.priority === "urgent" ? "urgent" : "high priority"})`}`,
        event.priority === "urgent" ? "urgent" : "");
      refreshSoon();
      if (currentTab === "tasks") loadTasks();
      break;
    case "task_updated":
      refreshSoon();
      if (currentTab === "tasks" && Date.now() > taskBusyUntil) loadTasks();
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
      st.operator = Boolean(event.active);
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
  if (currentTab === "today") renderTodayLive();
}

function renderCallSwitcher() {
  const box = $("live-calls");
  const inner = box.firstElementChild;
  const active = [...liveCalls.values()].filter((st) => !st.ended || st === live);
  const shown = active.length >= 2;
  const chips = active.map((st, i) => el("button", {
    type: "button", class: `chip${st === live ? " active" : ""}`, title: st.callId, "aria-pressed": String(st === live),
    text: `Call ${i + 1}${st.ended ? " (ended)" : ""}`, onclick: () => followCall(st.callId),
  }));
  inner.replaceChildren(...chips);
  // The row opens and closes with a height change (grid rows 0fr to 1fr), never a jump.
  box.classList.toggle("is-shown", shown);
  box.inert = !shown;
  // A call that has just come in pops its chip in (B5).
  const fresh = chips.filter((_, i) => !switcherShown.has(active[i].callId));
  if (shown && fresh.length) popIn(fresh);
  switcherShown = new Set(shown ? active.map((st) => st.callId) : []);
}
let switcherShown = new Set();

const STATE_LABEL = { idle: "Idle", listening: "Listening", thinking: "Thinking", speaking: "Speaking", ended: "Call ended" };

// What the state pill says: staff on the call and a call being ended win over Emma's own state.
function liveStatus(st) {
  if (st.ended) return { cls: "ended", text: st.endedByMe ? "Ended by you" : "Call ended" };
  if (st.ending) return { cls: "ending", text: "Ending the call…" };
  if (st.operator) return { cls: "staff-on", text: "You're on the call" };
  return { cls: st.state, text: STATE_LABEL[st.state] || "On a call" };
}

function bubbleLines(lines, interim, labels = ROLE_LABEL) {
  const nodes = [];
  let prev = null;
  for (const line of lines) {
    nodes.push(el("div", { class: `line ${line.who}${prev === line.who ? " same" : ""}` },
      el("span", { class: "who", text: labels[line.who] || "Caller" }),
      el("div", { class: "bubble", text: line.text })));
    prev = line.who;
  }
  if (interim) {
    nodes.push(el("div", { class: `line caller interim${prev === "caller" ? " same" : ""}` },
      el("span", { class: "who", text: "Caller (still talking)" }),
      el("div", { class: "bubble", text: interim })));
  }
  return nodes;
}

// Who is on the line, as far as Emma knows yet.
function callerText(st) {
  const e = st.entities || {};
  const name = e.name || e.patient_name || e.caller_name;
  const phone = e.phone || e.phone_number || e.caller_phone;
  return [name, phone].filter(Boolean).join(" · ") || (st.callId ? "Not given yet" : "–");
}

let renderedLineCount = 0;
let renderedFor = null;
let typingShown = false;
const entityShown = new WeakMap();      // live state -> entity keys already on screen
function renderLive() {
  renderCallSwitcher();
  const hasCall = Boolean(live.callId);
  $("live-empty").hidden = hasCall;
  $("live-transcript").hidden = !hasCall;
  const status = liveStatus(live);
  for (const id of ["live-state", "live-state-bar"]) {
    const pill = $(id);
    pill.className = `state-pill ${status.cls}`;
    pill.textContent = status.text;
  }
  $("live-call").textContent = callerText(live);
  $("live-goal").textContent = live.ended ? "Call over" : (goalText(live.goal) || (hasCall ? "On a call" : "–"));
  $("live-outcome").textContent = live.ended && live.endedByMe && (!live.outcome || live.outcome === "ended_by_staff")
    ? "Ended by you" : live.outcome ? callOutcome(live.outcome) : (hasCall ? "In progress" : "–");
  renderDuration();
  // Controls stop the moment staff end the call, not when the goodbye has finished.
  const active = hasCall && !live.ended && !live.ending;
  $("live-controls").hidden = !active;
  $("live-controls").classList.toggle("is-operator", live.operator);
  $("op-takeover").inert = live.operator;
  $("op-handback").inert = !live.operator;
  const composing = active && live.operator;
  $("live-chat").classList.toggle("composing", composing);
  $("compose-dock").inert = !composing;

  const box = $("live-transcript");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const nodes = bubbleLines(live.lines, live.interim, LIVE_ROLE_LABEL);
  // Messages: only a bubble that wasn't on screen before springs in (opacity and an 8px rise).
  // One that is re-rendered, such as Emma's line growing sentence by sentence, stays put.
  const total = live.lines.length + (live.interim ? 1 : 0);
  const sameCall = renderedFor === live;
  nodes.forEach((node, i) => { if (sameCall && i < renderedLineCount) node.classList.add("settled"); });
  renderedLineCount = total;
  renderedFor = live;
  const typing = active && !live.operator && live.state === "thinking";
  if (typing) {
    const dots = el("div", { class: `typing${typingShown && sameCall ? " settled" : ""}`, role: "img", "aria-label": "Emma is thinking" },
      el("i"), el("i"), el("i"));
    nodes.push(dots);
  }
  typingShown = typing;
  box.replaceChildren(...nodes);
  if (nearBottom) {
    box.scrollTop = box.scrollHeight;
    $("live-jump").hidden = true;
  } else if (hasCall) {
    $("live-jump").hidden = false;
  }

  // What Emma has collected: a chip pops in (B5) the moment a new detail arrives.
  const entities = liveDetails(live.entities);
  const shownBefore = entityShown.get(live) || new Set();
  const chips = entities.map(([k, v]) => el("span", { class: "entity" },
    el("b", { text: ENTITY_LABEL[k] }), v));
  $("live-entities").replaceChildren(...chips);
  const fresh = chips.filter((_, i) => !shownBefore.has(entities[i][0]));
  if (fresh.length && entityShown.has(live)) popIn(fresh);
  entityShown.set(live, new Set(entities.map(([k]) => k)));

  const rows = [...live.turns.values()].sort((a, b) => b.turn - a.turn).slice(0, 30).map((t) => el("tr", {},
    el("td", { class: "num", text: t.turn }),
    el("td", { text: TIER_LABEL[t.tier] || (t.tier === -1 ? "Greeting" : "–") }),
    el("td", { class: "num", text: ms(t.perceived_ms) }),
    el("td", { class: "num", text: ms(t.endpoint_ms) }),
    el("td", { class: "num", text: ms(t.nlu_ms) }),
    el("td", { class: "num", text: ms(t.first_audio_ms) })));
  $("live-turns").replaceChildren(...(rows.length ? rows : [emptyRow(6, "Nothing yet: times appear after Emma's first reply.")]));
}
// What Emma has understood so far, in front-desk words. Only these keys are shown, in this
// order, at most four; internal keys (intent, source, raw text, phrases) never appear.
const ENTITY_LABEL = { name: "Name", patient_name: "Name", phone: "Phone", service: "Service", date: "Preferred day",
  day: "Preferred day", preferred_day: "Preferred day", time: "Time", doctor: "Doctor", branch: "Branch", age: "Age",
  patient_age: "Age" };
const NOTHING = new Set(["", "none", "null", "unknown", "false", "no", "n/a"]);
function liveDetails(entities) {
  const out = [];
  const seen = new Set();
  for (const key of Object.keys(ENTITY_LABEL)) {
    const v = (entities || {})[key];
    if (v === null || v === undefined || typeof v === "object" || NOTHING.has(String(v).trim().toLowerCase())) continue;
    const label = ENTITY_LABEL[key];
    if (seen.has(label)) continue;
    seen.add(label);
    const text = /^\d{4}-\d{2}-\d{2}$/.test(String(v)) ? dayFmt(String(v), { weekday: "short", day: "numeric", month: "short" }) : String(v);
    out.push([key, text]);
    if (out.length === 4) break;
  }
  return out;
}

$("live-transcript").addEventListener("scroll", () => {
  const box = $("live-transcript");
  if (box.scrollHeight - box.scrollTop - box.clientHeight < 80) $("live-jump").hidden = true;
});
$("live-jump").addEventListener("click", () => {
  const box = $("live-transcript");
  box.scrollTop = box.scrollHeight;
  $("live-jump").hidden = true;
});

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
    setTimeout(() => $("op-text").focus({ preventScroll: true }), reducedMotion() ? 0 : 120);
  }
});
$("op-handback").addEventListener("click", async () => {
  const res = await liveAction("handback");
  if (res && res.ok) { live.operator = false; renderLive(); $("op-takeover").focus({ preventScroll: true }); }
});
$("op-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("op-text").value.trim();
  if (!text) return;
  const st = live;
  // Remember it before sending: its caption can arrive before the reply does.
  st.staffSaid.push(norm(text));
  const res = await liveAction("say", { text });
  if (res && res.ok) {
    $("op-text").value = "";
    for (const line of st.lines) if (line.who === "emma" && isStaffLine(st, line.text)) line.who = "operator";
    if (currentTab === "live") renderLive();
  } else {
    st.staffSaid = st.staffSaid.filter((t) => t !== norm(text));
  }
});
$("op-end").addEventListener("click", async () => {
  if (!await confirmAction("End this call?", "Emma says goodbye and promises a call back. A callback task is added to Tasks.", "End call")) return;
  const st = live;
  st.ending = true;            // the controls go away now; the call ends after Emma's goodbye
  st.endedByMe = true;
  renderLive();
  const res = await liveAction("end", {});
  if (!res || !res.ok) {
    st.ending = false;
    st.endedByMe = false;
    if (currentTab === "live") renderLive();
  }
});

function renderDuration() {
  if (!live.started) { $("live-duration").textContent = "–"; return; }
  const end = live.ended ? (live.endedAt || new Date()).toISOString() : null;
  $("live-duration").textContent = length(live.started.toISOString(), end);
}
setInterval(() => {
  if (currentTab === "live" && live.callId && !live.ended) renderDuration();
  if (callBusy) renderSideTimer();
}, 1000);

const EVENT_TYPES = ["call_started", "caption", "state", "turn", "metrics", "call_turn", "call_ended", "task_created",
  "task_updated", "appointment", "sync", "call_data_deleted", "bye", "error", "recovery", "ring", "ring_ended",
  "operator"];

function connectEvents() {
  const source = new EventSource("/dashboard/api/events");
  let failures = 0;
  source.addEventListener("open", () => {
    failures = 0;
    lastSeq = 0;                // the server replays the calls in progress from the start
    resetLive();
    renderedLineCount = 0;
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

// ------------------------------------------------------------------ Today (apple.com tile grid)
function tileHead(icon, label) {
  return el("h2", { class: "tile-kicker" }, svgIcon(icon), label);
}
// The tile's one link, stretched over the whole tile (its ::after), so the tile reads as a
// card with a heading and content rather than one giant button.
function tileFoot(go, text) {
  return el("a", { class: "tile-foot", href: `#${go}`, "data-go": go, text });
}

function renderTodayLive() {
  const tile = $("today-live");
  const running = [...liveCalls.values()].find((st) => !st.ended) || null;
  tile.classList.toggle("live-on", Boolean(running));
  if (!running) {
    const calls = todayData.calls || [];
    const today = clinicToday();
    const todays = calls.filter((c) => (c.started_at || "").slice(0, 10) === today || fmt(c.started_at, { year: "numeric", month: "2-digit", day: "2-digit" }) === dayFmt(today, { year: "numeric", month: "2-digit", day: "2-digit" }));
    tile.replaceChildren(tileHead("i-live", "Live call"),
      el("span", { class: "tile-headline", text: "No call right now" }),
      el("span", { class: "tile-sub", text: todays.length ? `${todays.length} call${todays.length === 1 ? "" : "s"} so far today.` : "Emma is ready for the next caller." }),
      tileFoot("live", "Open live call"));
    return;
  }
  const last = running.lines[running.lines.length - 1];
  const status = liveStatus(running);
  tile.replaceChildren(tileHead("i-live", "Live call"),
    el("span", { class: "tile-headline with-dot" }, el("span", { class: "live-dot" }), status.text),
    el("span", { class: "tile-sub", text: running.operator ? "Staff are talking to the caller" : (goalText(running.goal) || "Emma is on a call") }),
    last ? el("p", { class: "tile-line" }, el("span", { class: "tile-line-who", text: `${LIVE_ROLE_LABEL[last.who] || "Caller"}: ` }), last.text) : "",
    tileFoot("live", "Follow the call"));
}

const todayData = { appts: [], tasks: [], calls: [], recovery: null, health: null, perf: null };

async function renderToday() {
  const today = clinicToday();
  $("today-date").textContent = prettyDay(today);
  const [appts, tasks, calls, recovery, health, perf] = await Promise.allSettled([
    api(`/dashboard/api/appointments?scope=day&day=${today}`),
    api("/dashboard/api/tasks?status=open"),
    api("/dashboard/api/calls?limit=40"),
    api("/dashboard/api/recovery"),
    fetch("/health", { credentials: "same-origin" }).then((r) => r.json()),
    api("/dashboard/api/performance?days=1"),
  ]);
  todayData.perf = perf.status === "fulfilled" ? perf.value : null;
  if (appts.status === "fulfilled") todayData.appts = appts.value.appointments;
  if (tasks.status === "fulfilled") todayData.tasks = tasks.value.tasks;
  if (calls.status === "fulfilled") todayData.calls = calls.value.calls;
  if (recovery.status === "fulfilled") todayData.recovery = recovery.value;
  todayData.health = health.status === "fulfilled" ? health.value : null;
  paintToday();
}
const renderTodaySoon = debounce(renderToday, 600);

function paintToday() {
  renderTodayLive();

  // Appointments: the next four as a mini timeline.
  const rows = todayData.appts;
  const active = rows.filter((r) => r.status === "booked" || r.status === "needs_reschedule");
  const now = new Date();
  const upcoming = active.filter((r) => new Date(r.end) > now);
  const next = upcoming.slice(0, 4);
  const done = rows.filter((r) => r.status === "completed").length;
  const cancelled = rows.filter((r) => r.status === "cancelled").length;
  $("side-appt-count").textContent = String(active.length);
  $("side-appt-count").hidden = active.length === 0;
  $("today-appts").replaceChildren(tileHead("i-calendar", "Appointments today"),
    el("span", { class: "tile-big", text: String(active.length) }),
    el("span", { class: "tile-sub", text: [`${done} attended`, `${cancelled} cancelled`,
      rows.some((r) => r.status === "needs_reschedule") ? "some need a new time" : null].filter(Boolean).join(" · ") }),
    next.length ? el("ul", { class: "tile-list" }, ...next.map((r) => el("li", {},
      el("span", { class: "t", text: r.time }),
      el("span", { class: "x", text: `${r.patient_name} · ${r.service} · ${r.doctor}` })))) : el("span", { class: "tile-sub", text: "Nothing else booked today." }),
    upcoming.length > next.length ? el("span", { class: "tile-sub", text: `${upcoming.length - next.length} more` }) : "",
    tileFoot("appointments", "Open calendar"));

  // Tasks.
  const tasks = todayData.tasks;
  const urgent = tasks.filter((t) => t.priority === "urgent" || t.priority === "high").length;
  $("today-tasks").replaceChildren(tileHead("i-tasks", "Open tasks"),
    el("span", { class: "tile-big", text: String(tasks.length) }),
    el("span", { class: "tile-sub", text: tasks.length ? (urgent ? `${urgent} need attention first` : "Callbacks Emma promised") : "Nothing waiting. Nice." }),
    el("ul", { class: "tile-list" }, ...tasks.slice(0, 3).map((t) => el("li", {},
      el("span", { class: "x", text: taskTitle(t) })))),
    tileFoot("tasks", "Open tasks"));

  // Recent calls.
  const calls = todayData.calls.slice(0, 3);
  $("today-calls").replaceChildren(tileHead("i-calls", "Recent calls"),
    calls.length ? el("ul", { class: "tile-list stacked" }, ...calls.map((c) => el("li", {},
      el("span", { class: "x", text: c.phone_display || `${CHANNEL_LABEL[c.channel] || "Unknown"} caller` }),
      el("span", { class: "o" }, el("span", { class: "t", text: fmtTime(c.started_at) }), ` · ${callOutcome(c.outcome)}`)))) : el("span", { class: "tile-sub", text: "No calls yet." }),
    tileFoot("calls", "Open calls"));

  // Recovery campaign progress.
  const rec = todayData.recovery;
  const campaign = rec && (rec.campaigns || []).find((c) => c.status === "running");
  const recTile = $("today-recovery");
  if (campaign) {
    const total = campaign.jobs.length || 1;
    const finished = campaign.jobs.filter((j) => ["done", "failed", "skipped"].includes(j.status)).length;
    const pct = Math.round((100 * finished) / total);
    const ring = el("div", { class: "ring", "data-label": `${finished}/${total}` });
    ring.style.setProperty("--p", String(pct));
    recTile.replaceChildren(tileHead("i-recovery", "Recovery calls"), ring,
      el("span", { class: "tile-sub", text: `${campaign.doctor}: ${summaryText(campaign)}` }),
      tileFoot("recovery", "Open recovery"));
    $("side-recovery-badge").textContent = String(total - finished);
    $("side-recovery-badge").hidden = total === finished;
  } else {
    const blocks = rec ? (rec.blocks || []).length : 0;
    recTile.replaceChildren(tileHead("i-recovery", "Recovery"),
      el("span", { class: "tile-headline", text: blocks ? `${blocks} doctor${blocks === 1 ? "" : "s"} blocked` : "All doctors available" }),
      el("span", { class: "tile-sub", text: "When a doctor can't come in, Emma calls their patients to find a new time." }),
      tileFoot("recovery", "Open recovery"));
    $("side-recovery-badge").hidden = true;
  }

  // System health.
  const h = todayData.health;
  const problems = [];
  if (!h) problems.push("Server unreachable");
  else {
    if (!h.deepgram) problems.push("Speech to text has no key");
    if (!h.elevenlabs) problems.push("Voice has no key");
    if (!h.gemini) problems.push("Understanding unavailable");
  }
  // One truth with the System page: if understanding kept falling back today, say so here too.
  const channels = Object.values((todayData.perf && todayData.perf.channels) || {});
  const model = channels.reduce((n, c) => n + (c.model || 0) + (c.fallback || 0), 0);
  const backup = channels.reduce((n, c) => n + (c.fallback || 0), 0);
  if (model >= 4 && backup / model > 0.25) problems.push(`Understanding was slow: backup replies on ${Math.round((100 * backup) / model)}% of turns today`);
  const failed = overview ? overview.sync.failed || 0 : 0;
  if (failed) problems.push(`${failed} calendar sync${failed === 1 ? "" : "s"} failed`);
  for (const a of (overview && overview.alerts) || []) problems.push(a.text);
  $("today-system").replaceChildren(tileHead("i-system", "System"),
    el("span", { class: "tile-headline with-dot" }, el("span", { class: `health-dot${problems.length ? " bad" : ""}` }),
      problems.length ? "Needs a look" : "All good"),
    el("span", { class: "tile-sub", text: problems.length ? problems.slice(0, 3).join(" · ") : "Speech, voice, understanding and calendar sync are working." }),
    tileFoot("system", "Open system"));
}

// ------------------------------------------------------------------ appointments (Calendar)
const VIEWS = ["day", "week", "month", "year", "list"];
const appt = {
  view: VIEWS.includes(recall("cal-view")) ? recall("cal-view") : "day",
  day: null, listScope: "upcoming", rows: [], slide: 0, hidden: new Set(JSON.parse(recall("cal-hidden") || "[]")), byDate: null,
};
const segmentPlacers = [];

function apptRange() {
  if (appt.view === "day") return { first: appt.day, days: 1 };
  if (appt.view === "week") return { first: startOfWeek(appt.day), days: 7 };
  if (appt.view === "month") return { first: startOfWeek(startOfMonth(appt.day)), days: 42 };
  return null;
}

function apptQuery(range = apptRange()) {
  const params = new URLSearchParams();
  if (appt.view === "list") params.set("scope", appt.listScope);
  else if (range) {
    params.set("scope", "day");
    params.set("day", range.first);
    params.set("days", String(range.days));
  }
  for (const [key, id] of [["branch_id", "appt-branch"], ["doctor_id", "appt-doctor"], ["status", "appt-status"]]) {
    if ($(id).value) params.set(key, $(id).value);
  }
  const q = $("appt-search").value.trim();
  if (q) params.set("q", q);
  return params;
}

let apptRequest = 0;
async function loadAppointments() {
  appt.day = appt.day || clinicToday();
  $("appt-day").value = appt.day;
  document.querySelectorAll("#cal-view button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === appt.view)));
  document.querySelectorAll("#appt-scope button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.scope === appt.listScope)));
  const listing = appt.view === "list";
  document.querySelectorAll(".list-only").forEach((node) => { node.hidden = !listing; });
  $("appt-day").hidden = listing;
  $("list-view").hidden = !listing;
  $("cal-body").hidden = listing;
  document.querySelector(".cal-nav").style.visibility = listing ? "hidden" : "visible";
  renderCalTitle();
  renderMiniMonth();
  const ticket = ++apptRequest;
  try {
    if (appt.view === "year") {
      const { y } = parts(appt.day);
      const starts = [0, 62, 124, 186, 248, 310].map((offset) => shiftDay(isoDate(y, 0, 1), offset));
      const chunks = await Promise.all(starts.map((first) => api(`/dashboard/api/appointments?${apptQuery({ first, days: 62 })}`)));
      appt.rows = chunks.flatMap((c) => c.appointments).filter((r) => r.date.startsWith(String(y)));
    } else {
      appt.rows = (await api(`/dashboard/api/appointments?${apptQuery()}`)).appointments;
    }
  } catch (err) {
    if (ticket !== apptRequest) return;
    $("cal-body").replaceChildren(el("div", { class: "cal-empty", text: err.message }));
    $("appt-rows").replaceChildren(emptyRow(9, err.message));
    return;
  }
  if (ticket !== apptRequest) return;
  renderAppointments();
}
const reloadAppointmentsSoon = debounce(loadAppointments, 400);

function visibleRows() {
  return appt.rows.filter((r) => !appt.hidden.has(r.doctor_id));
}

function renderCalTitle() {
  const main = $("cal-title-main");
  const sub = $("cal-title-sub");
  const { y } = parts(appt.day);
  if (appt.view === "day") {
    main.textContent = `${dayFmt(appt.day, { day: "numeric", month: "long" })} ${y}`;
    sub.textContent = dayFmt(appt.day, { weekday: "long" });
  } else if (appt.view === "week") {
    const first = startOfWeek(appt.day);
    const last = shiftDay(first, 6);
    main.textContent = dayFmt(first, { month: "long" });
    sub.textContent = parts(first).m === parts(last).m ? String(y) : `– ${dayFmt(last, { month: "long" })} ${parts(last).y}`;
  } else if (appt.view === "month") {
    main.textContent = dayFmt(appt.day, { month: "long" });
    sub.textContent = String(y);
  } else if (appt.view === "year") {
    main.textContent = String(y);
    sub.textContent = "";
  } else {
    main.textContent = "Appointments";
    sub.textContent = { upcoming: "Upcoming", past: "Past", all: "All" }[appt.listScope];
  }
  $("cal-bar-title").textContent = `${main.textContent} ${sub.textContent}`.trim();
}

function renderAppointments() {
  const rows = visibleRows();
  const active = rows.filter((r) => r.status === "booked" || r.status === "needs_reschedule").length;
  $("appt-summary").textContent = `${rows.length} shown, ${active} active`;
  const timeGrid = appt.view === "day" || appt.view === "week";
  $("cal-body").classList.toggle("scroller", timeGrid);
  $("cal-body").classList.toggle("fit", timeGrid);
  if (appt.view === "day") renderTimeGrid([appt.day], "doctors");
  else if (appt.view === "week") renderTimeGrid([...Array(7)].map((_, i) => shiftDay(startOfWeek(appt.day), i)), "days");
  else if (appt.view === "month") renderMonth();
  else if (appt.view === "year") renderYear();
  else renderList();
  // Prev / next: the new range slides in from the side it came from and cross-fades (300ms).
  const moved = appt.slide;
  appt.slide = 0;
  const first = $("cal-body").firstElementChild;
  if (moved && first && !$("cal-body").hidden) {
    first.classList.remove("slide-next", "slide-prev");
    void first.offsetWidth;
    first.classList.add(moved > 0 ? "slide-next" : "slide-prev");
  }
}

// The hour grid scrolls inside its own box, which fills the window below the toolbar, so the
// toolbar and its prev / Today / next never scroll away.
// The same for the Messages pane and the Mail list: each fills the window below its own top,
// so the page itself never scrolls a few pixels for nothing on top of an inner scroller.
function fitCalendar() {
  for (const node of document.querySelectorAll(".view:not([hidden]) .fit")) {
    if (node.hidden || !node.offsetParent) continue;
    const top = node.getBoundingClientRect().top + window.scrollY;
    node.style.setProperty("--fit-top", `${Math.round(top)}px`);
  }
}
window.addEventListener("resize", debounce(fitCalendar, 120));

// Lay overlapping events side by side, as Calendar does.
function layoutLanes(events) {
  const sorted = [...events].sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin);
  let cluster = [];
  let clusterEnd = -1;
  const flush = () => {
    const lanes = [];
    for (const ev of cluster) {
      let lane = lanes.findIndex((end) => end <= ev.startMin);
      if (lane < 0) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = ev.endMin;
      ev.lane = lane;
    }
    for (const ev of cluster) ev.lanes = lanes.length;
    cluster = [];
  };
  for (const ev of sorted) {
    if (ev.startMin >= clusterEnd && cluster.length) flush();
    cluster.push(ev);
    clusterEnd = Math.max(clusterEnd, ev.endMin);
  }
  if (cluster.length) flush();
  return sorted;
}

const minutesOf = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };

function eventButton(r, ev, firstHour, hourPx) {
  const top = ((ev.startMin - firstHour * 60) / 60) * hourPx;
  const height = Math.max(22, ((ev.endMin - ev.startMin) / 60) * hourPx - 2);
  const width = 100 / ev.lanes;
  const node = el("button", {
    type: "button", class: `event ${r.status}`, "data-appt": r.id,
    title: `${r.time}–${r.end_time} ${r.patient_name}, ${r.service}, ${r.doctor}, ${r.branch}`,
  },
  el("span", { class: "ev-name", text: r.patient_name }),
  height > 34 ? el("span", { class: "ev-meta", text: `${r.time} · ${r.service}` }) : null,
  height > 52 ? el("span", { class: "ev-meta", text: appt.view === "day" ? r.branch : r.doctor }) : null);
  node.style.setProperty("--doc", doctorColor(r.doctor_id));
  node.style.top = `${top}px`;
  node.style.height = `${height}px`;
  node.style.left = `calc(${ev.lane * width}% + 3px)`;
  node.style.right = "auto";
  node.style.width = `calc(${width}% - 6px)`;
  node.addEventListener("click", (event) => { event.stopPropagation(); openPopover(r, node); });
  return node;
}

function renderTimeGrid(daysList, mode) {
  const rows = visibleRows();
  let first = dayHours[0];
  let last = dayHours[1];
  for (const r of rows) {
    first = Math.min(first, Math.floor(minutesOf(r.time) / 60));
    last = Math.max(last, Math.ceil(minutesOf(r.end_time) / 60));
  }
  const hourPx = window.matchMedia("(max-width: 734px)").matches ? 56 : 64;

  // Columns: in Day view one per doctor (filtered by branch), in Week view one per day.
  let columns;
  if (mode === "doctors") {
    const branch = Number($("appt-branch").value) || null;
    const chosenDoctor = Number($("appt-doctor").value) || null;
    let doctors = catalog.doctors.filter((d) => (!branch || d.branch_id === branch) && (!chosenDoctor || d.id === chosenDoctor)
      && !appt.hidden.has(d.id));
    const withRows = new Set(rows.map((r) => r.doctor_id));
    if (doctors.length > 6) doctors = doctors.filter((d) => withRows.has(d.id));
    if (!doctors.length) doctors = catalog.doctors.filter((d) => withRows.has(d.id));
    const phone = window.matchMedia("(max-width: 734px)").matches;
    columns = doctors.map((d) => ({ key: d.id, label: phone ? `Dr ${surname(d.name)}` : d.name.replace(/^Dr\.? /, "Dr "), sub: d.branch, day: daysList[0],
      rows: rows.filter((r) => r.doctor_id === d.id), color: doctorColor(d.id) }));
    if (!columns.length) columns = [{ key: "none", label: "", sub: "", day: daysList[0], rows: [] }];
  } else {
    columns = daysList.map((iso) => ({ key: iso, day: iso, rows: rows.filter((r) => r.date === iso),
      label: dayFmt(iso, { weekday: "short" }), num: parts(iso).d }));
  }

  const today = clinicToday();
  const grid = el("div", { class: `tgrid${mode === "doctors" && columns.length > 2 ? " many" : ""}` });
  grid.style.setProperty("--cols", String(columns.length));
  grid.style.setProperty("--hour", `${hourPx}px`);
  const head = el("div", { class: "tgrid-head" }, el("div", { class: "col-head" }));
  for (const col of columns) {
    if (mode === "doctors") {
      const h = el("div", { class: "col-head doctor" },
        el("b", {}, col.color ? el("i", { class: "doc-dot", "aria-hidden": "true" }) : null, col.label || "No doctors"),
        col.sub ? col.sub : "");
      if (col.color) h.style.setProperty("--doc", col.color);
      head.append(h);
    } else {
      const button = el("button", { type: "button", "aria-label": `Open ${prettyDay(col.day)}` }, col.label, el("b", { text: String(col.num) }));
      button.addEventListener("click", () => { appt.day = col.day; setView("day"); });
      head.append(el("div", { class: `col-head${col.day === today ? " today" : ""}` }, button));
    }
  }
  grid.append(head);
  const hoursCol = el("div", { class: "hours" });
  for (let h = first; h < last; h += 1) hoursCol.append(el("div", { class: "hour-label", text: h === first ? "" : `${String(h).padStart(2, "0")}:00` }));
  const nowMin = clinicNowMinutes();
  if (columns.some((c) => c.day === today) && nowMin >= first * 60 && nowMin <= last * 60) {
    // Calendar's "now": the time in red in the gutter, one red line through today.
    const chip = el("span", { class: "now-chip num", text: `${String(Math.floor(nowMin / 60)).padStart(2, "0")}:${String(nowMin % 60).padStart(2, "0")}` });
    chip.style.top = `${((nowMin - first * 60) / 60) * hourPx}px`;
    hoursCol.append(chip);
  }
  grid.append(hoursCol);
  for (const col of columns) {
    const sunday = weekday(col.day) === 6;
    const colNode = el("div", { class: `day-col${sunday ? " closed" : ""}` });
    colNode.style.height = `${(last - first) * hourPx}px`;
    const events = layoutLanes(col.rows.map((r) => ({ r, startMin: minutesOf(r.time), endMin: minutesOf(r.end_time) })));
    for (const ev of events) colNode.append(eventButton(ev.r, ev, first, hourPx));
    if (col.day === today) {
      const now = clinicNowMinutes();
      if (now >= first * 60 && now <= last * 60) {
        const line = el("div", { class: `now-line${col === columns.find((c) => c.day === today) ? " first" : ""}` });
        line.style.top = `${((now - first * 60) / 60) * hourPx}px`;
        colNode.append(line);
      }
    }
    grid.append(colNode);
  }
  const body = $("cal-body");
  const notes = [];
  if (!rows.length) {
    const sunday = appt.view === "day" && weekday(appt.day) === 6;
    notes.push(el("div", { class: "cal-empty", text: sunday ? "The clinic is closed on Sundays." : "No appointments here." }));
  }
  body.replaceChildren(grid, ...notes);
  fitCalendar();
  if (mode === "doctors" || columns.some((c) => c.day === today)) {
    // Start the grid near "now" (or the first appointment), as Calendar does: the grid
    // scrolls, the page and its toolbar stay put.
    const onToday = columns.some((c) => c.day === today);
    const target = onToday ? clinicNowMinutes() : (rows.length ? Math.min(...rows.map((r) => minutesOf(r.time))) : first * 60);
    body.scrollTop = Math.max(0, ((target - first * 60) / 60) * hourPx - 120);
  }
}

function renderMonth() {
  const rows = visibleRows();
  const first = startOfWeek(startOfMonth(appt.day));
  const month = parts(appt.day).m;
  const today = clinicToday();
  const byDate = new Map();
  for (const r of rows) (byDate.get(r.date) || byDate.set(r.date, []).get(r.date)).push(r);
  const grid = el("div", { class: "mgrid" }, ...["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => el("div", { class: "dow", text: d })));
  for (let i = 0; i < 42; i += 1) {
    const iso = shiftDay(first, i);
    const list = (byDate.get(iso) || []).sort((a, b) => a.time.localeCompare(b.time));
    const cell = el("div", { class: `mcell${parts(iso).m !== month ? " other" : ""}${iso === today ? " today" : ""}`,
      role: "button", tabindex: "0", "aria-label": `${prettyDay(iso)}, ${list.length} appointment${list.length === 1 ? "" : "s"}` },
    el("span", { class: "mnum", text: String(parts(iso).d) }),
    ...list.slice(0, 3).map((r) => {
      const item = el("div", { class: `mev ${r.status}` }, el("i"), el("span", { class: "mt", text: r.time }), el("span", { class: "mn", text: ` ${r.patient_name}` }));
      item.style.setProperty("--doc", doctorColor(r.doctor_id));
      return item;
    }),
    list.length > 3 ? el("div", { class: "mmore", text: `${list.length - 3} more` }) : null);
    const open = () => { appt.day = iso; setView("day"); };
    cell.addEventListener("click", open);
    cell.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    grid.append(cell);
  }
  $("cal-body").replaceChildren(grid);
}

function renderYear() {
  const rows = visibleRows();
  const has = new Set(rows.map((r) => r.date));
  const { y } = parts(appt.day);
  const today = clinicToday();
  const grid = el("div", { class: "ygrid" });
  for (let m = 0; m < 12; m += 1) {
    const firstIso = isoDate(y, m, 1);
    const daysIn = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const cells = ["M", "T", "W", "T", "F", "S", "S"].map((d) => el("span", { class: "dow", text: d }));
    for (let i = 0; i < weekday(firstIso); i += 1) cells.push(el("span"));
    for (let d = 1; d <= daysIn; d += 1) {
      const iso = isoDate(y, m, d);
      const b = el("button", { type: "button", class: `${has.has(iso) ? "has" : ""}${iso === today ? " today" : ""}`, text: String(d),
        "aria-label": prettyDay(iso) });
      b.addEventListener("click", () => { appt.day = iso; setView("day"); });
      cells.push(b);
    }
    const title = el("h3", {}, el("button", { type: "button", class: "btn-pill text small", text: dayFmt(firstIso, { month: "long" }),
      onclick: () => { appt.day = firstIso; setView("month"); } }));
    title.firstChild.style.color = "inherit";
    title.firstChild.style.padding = "0";
    grid.append(el("div", { class: "ymonth" }, title, el("div", { class: "ydays" }, ...cells)));
  }
  $("cal-body").replaceChildren(grid);
}

function renderList() {
  const tbody = $("appt-rows");
  const group = $("appt-group").value;
  const rows = [...visibleRows()];
  if (group) rows.sort((a, b) => a[group].localeCompare(b[group]) || a.start.localeCompare(b.start));
  if (!rows.length) {
    tbody.replaceChildren(emptyRow(9, "No appointments match."));
    return;
  }
  const out = [];
  let lastGroup = null;
  for (const r of rows) {
    if (group && r[group] !== lastGroup) {
      lastGroup = r[group];
      out.push(el("tr", { class: "group-row" }, el("td", { colspan: 9, text: lastGroup })));
    }
    out.push(el("tr", { class: r.status === "cancelled" ? "muted-row" : "" },
      el("td", { class: "nowrap num c-when", text: fmtDateTime(r.start) }),
      el("td", { class: "c-patient" }, r.patient_name, r.patient_age ? el("span", { class: "ink-2 t-caption", text: ` · ${r.patient_age} y` }) : null,
        r.name_unverified ? el("div", {}, el("span", { class: "badge warn", text: "name unverified" })) : null),
      el("td", { class: "nowrap num c-phone", text: r.phone_display }),
      el("td", { class: "c-service", text: r.service }),
      el("td", { class: "c-doctor", text: r.doctor }),
      el("td", { class: "c-branch", text: r.branch }),
      el("td", { class: "c-status" }, el("span", { class: `badge ${r.status}`, text: STATUS_LABEL[r.status] || "Booked" }),
        r.cancel_reason ? el("div", { class: "ink-2 t-caption", text: r.cancel_reason }) : null),
      el("td", { class: "c-cal", title: r.calendar_synced ? "Added to Google Calendar" : "Not added to Google Calendar yet", text: r.calendar_synced ? "✓" : "…" }),
      el("td", { class: "actions" }, ...apptActions(r, true))));
  }
  tbody.replaceChildren(...out);
}

// The actions an appointment allows: Move/Cancel ahead of time, Attended/No-show once it has started.
function apptActions(r, small = false) {
  const now = new Date();
  const changeable = (r.status === "booked" || r.status === "needs_reschedule") && new Date(r.start) > now;
  const size = small ? " small" : " small";
  if (changeable) {
    return [
      el("button", { class: `btn-pill gray${size}`, type: "button", text: "Move", onclick: () => { closePopover(); openBook("move", r); } }),
      el("button", { class: `btn-pill danger-text${size}`, type: "button", text: "Cancel", onclick: () => { closePopover(); openCancel(r); } }),
    ];
  }
  if (r.status === "booked" && new Date(r.start) <= now) {
    return [
      el("button", { class: `btn-pill${size}`, type: "button", text: "Attended", onclick: () => { closePopover(); markAttendance(r, "completed"); } }),
      el("button", { class: `btn-pill gray${size}`, type: "button", text: "No-show", onclick: () => { closePopover(); markAttendance(r, "no_show"); } }),
    ];
  }
  return [];
}

// Calendar's event inspector, as a sheet (B6): the details and what can be done next.
function openPopover(r, anchor) {
  const pop = $("appt-popover");
  document.querySelectorAll(".event.selected").forEach((n) => n.classList.remove("selected"));
  anchor.classList.add("selected");
  pop.style.setProperty("--doc", doctorColor(r.doctor_id));
  $("pop-title").textContent = r.patient_name + (r.patient_age ? `, ${r.patient_age}` : "");
  $("pop-when").textContent = `${prettyDay(r.date)}, ${r.time}–${r.end_time}`;
  const row = (k, v) => [el("dt", { text: k }), el("dd", {}, v)];
  $("pop-details").replaceChildren(
    ...row("Service", r.service),
    ...row("Doctor", r.doctor),
    ...row("Branch", r.branch),
    ...row("Phone", r.phone_display || "–"),
    ...row("Status", el("span", { class: `status-text ${r.status}`, text: STATUS_LABEL[r.status] || "Booked" })),
    ...(r.cancel_reason ? row("Reason", r.cancel_reason) : []),
    ...(r.name_unverified ? row("Name", el("span", { class: "status-text needs_reschedule", text: "Spelling not checked yet" })) : []),
    ...row("Google Calendar", r.calendar_synced ? "Added" : "Not added yet"),
    ...row("Booked by", r.created_by_call_id ? "Emma, on a call" : (SOURCE_LABEL[r.source] || "The clinic")));
  const actions = apptActions(r);
  if (r.created_by_call_id) {
    actions.push(el("button", { class: "btn-pill text small", type: "button", text: "Open the call",
      onclick: () => { closePopover(); showTab("calls"); openCall(r.created_by_call_id); } }));
  }
  $("pop-actions").replaceChildren(...actions);
  $("pop-actions").hidden = !actions.length;
  sheets.inspector.open(() => {
    document.querySelectorAll(".event.selected").forEach((n) => n.classList.remove("selected"));
  });
}
// Close the inspector (when an action in it opens the next sheet, or the section changes).
// The sheet returns focus to the event that opened it.
function closePopover() {
  if (sheets.inspector.isOpen) sheets.inspector.close();
}
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("more-sheet").hidden) { $("more-sheet").hidden = true; $("tab-more").focus(); }
});

// Calendar's sidebar: a mini month and the doctors as show/hide "calendars".
let miniMonthOf = null;
function renderMiniMonth() {
  const box = $("mini-month");
  const focus = miniMonthOf || startOfMonth(appt.day);
  const month = parts(focus).m;
  const first = startOfWeek(focus);
  const today = clinicToday();
  const grid = el("div", { class: "mini-grid" }, ...["M", "T", "W", "T", "F", "S", "S"].map((d) => el("span", { class: "dow", text: d })));
  for (let i = 0; i < 42; i += 1) {
    const iso = shiftDay(first, i);
    const cls = [parts(iso).m !== month ? "other" : "", iso === today ? "today" : "", iso === appt.day ? "chosen" : ""].filter(Boolean).join(" ");
    grid.append(el("button", { type: "button", class: cls, text: String(parts(iso).d), "aria-label": prettyDay(iso),
      onclick: () => { appt.day = iso; miniMonthOf = null; if (appt.view === "year" || appt.view === "list") appt.view = "day"; loadAppointments(); } }));
  }
  box.replaceChildren(el("div", { class: "mini-head" },
    el("span", { text: dayFmt(focus, { month: "long", year: "numeric" }) }),
    el("span", {},
      el("button", { type: "button", "aria-label": "Previous month", text: "‹", onclick: () => { miniMonthOf = shiftMonth(focus, -1); renderMiniMonth(); } }),
      el("button", { type: "button", "aria-label": "Next month", text: "›", onclick: () => { miniMonthOf = shiftMonth(focus, 1); renderMiniMonth(); } }))),
  grid);
}

function renderDoctorToggles() {
  const box = $("cal-doctors");
  box.replaceChildren(...catalog.doctors.map((d) => {
    const button = el("button", { type: "button", class: "doc-toggle", "aria-pressed": String(!appt.hidden.has(d.id)),
      title: `Show or hide ${d.name}` },
    el("span", { class: "doc-check" }), el("span", { text: d.name }), el("small", { text: d.branch }));
    button.style.setProperty("--doc", doctorColor(d.id));
    button.addEventListener("click", () => {
      if (appt.hidden.has(d.id)) appt.hidden.delete(d.id); else appt.hidden.add(d.id);
      store("cal-hidden", JSON.stringify([...appt.hidden]));
      button.setAttribute("aria-pressed", String(!appt.hidden.has(d.id)));
      renderAppointments();
    });
    return button;
  }));
}

function setView(view) {
  appt.view = view;
  store("cal-view", view);
  window.scrollTo({ top: 0 });
  loadAppointments();
}

function step(direction) {
  appt.slide = direction;
  if (appt.view === "day") appt.day = shiftDay(appt.day, direction);
  else if (appt.view === "week") appt.day = shiftDay(appt.day, 7 * direction);
  else if (appt.view === "month") appt.day = shiftMonth(appt.day, direction);
  else if (appt.view === "year") appt.day = shiftMonth(appt.day, 12 * direction);
  miniMonthOf = null;
  loadAppointments();
}

document.querySelectorAll("#cal-view button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("#appt-scope button").forEach((button) => button.addEventListener("click", () => {
  appt.listScope = button.dataset.scope;
  loadAppointments();
}));
$("appt-day").addEventListener("change", () => {
  appt.day = $("appt-day").value || clinicToday();
  if (appt.view === "list") appt.view = "day";
  loadAppointments();
});
$("appt-prev").addEventListener("click", () => step(-1));
$("appt-next").addEventListener("click", () => step(1));
$("appt-today").addEventListener("click", () => { appt.day = clinicToday(); miniMonthOf = null; loadAppointments(); });
$("appt-branch").addEventListener("change", () => { fillDoctorFilter(); loadAppointments(); });
$("appt-doctor").addEventListener("change", loadAppointments);
$("appt-status").addEventListener("change", loadAppointments);
$("appt-group").addEventListener("change", renderAppointments);
$("appt-search").addEventListener("input", debounce(() => {
  // Searching across all dates reads better as a list, as in Calendar's search results.
  if ($("appt-search").value.trim() && appt.view !== "list") { appt.view = "list"; appt.listScope = "all"; }
  loadAppointments();
}, 300));
$("appt-export").addEventListener("click", () => {
  // A plain navigation: the browser downloads the file with the session cookie.
  location.href = `/dashboard/api/appointments.csv?${apptQuery()}`;
});
$("appt-new").addEventListener("click", () => openBook("book"));
// Phones: the filters fold away behind one Filters button.
$("cal-filters-toggle").addEventListener("click", () => {
  const open = !$("cal-filters").classList.contains("is-open");
  $("cal-filters").classList.toggle("is-open", open);
  $("cal-filters-toggle").setAttribute("aria-expanded", String(open));
  requestAnimationFrame(fitCalendar);
});
document.addEventListener("keydown", (event) => {
  if (currentTab !== "appointments" || (event.target.closest && event.target.closest("input, select, textarea, .e-sheet-root"))
    || document.querySelector(".e-sheet-root.is-open")) return;
  if (event.key === "ArrowLeft") step(-1);
  else if (event.key === "ArrowRight") step(1);
  else if (event.key.toLowerCase() === "t") $("appt-today").click();
});

async function markAttendance(r, status) {
  try {
    await api(`/dashboard/api/appointments/${encodeURIComponent(r.id)}/attendance`, { method: "POST", body: { status } });
    toast(`${r.patient_name}: ${STATUS_LABEL[status]}.`);
    loadAppointments();
  } catch (err) {
    toast(err.message, "error");
  }
}

function describe(r) {
  return `${r.patient_name}: ${r.service} with ${r.doctor}, ${fmtDateTime(r.start)} at ${r.branch}`;
}

let cancelTarget = null;
function openCancel(r) {
  cancelTarget = r;
  $("cancel-what").textContent = describe(r);
  $("cancel-reason").value = "";
  showError($("cancel-error"), "");
  sheets.cancel.open();
}

$("cancel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("cancel-confirm");
  button.disabled = true;
  try {
    await api(`/dashboard/api/appointments/${encodeURIComponent(cancelTarget.id)}/cancel`, {
      method: "POST", body: { reason: $("cancel-reason").value, version: cancelTarget.version, idem_key: newKey() },
    });
    sheets.cancel.close();
    toast("Appointment cancelled.");
    loadAppointments();
  } catch (err) {
    showError($("cancel-error"), err.message);
  } finally {
    button.disabled = false;
  }
});

// Book and move share one sheet: pick where and when, then who (book only).
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
  $("book-slots").hidden = true;
  updateBookButton();
}

function updateBookButton() {
  const needsPatient = booking.mode === "book";
  const ready = booking.slot && (!needsPatient || ($("book-name").value.trim() && $("book-phone").value.trim()));
  $("book-confirm").disabled = !ready;
  const slot = booking.slot;
  $("book-chosen").textContent = slot ? `${dayFmt($("book-day").value, { weekday: "short", day: "numeric", month: "short" })}, ${slot.time} with ${slot.doctor}`
    : "Pick a free time above.";
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
    const day = appt.day && appt.day >= clinicToday() && appt.view !== "list" ? appt.day : clinicToday();
    $("book-day").value = day;
    for (const id of ["book-name", "book-phone", "book-age"]) $(id).value = "";
  }
  showError($("book-error"), "");
  clearSlots();
  // B22: the + turns 45 degrees while its sheet is open. The sheet follows 120ms later, so the
  // turn is seen before the scrim covers the button.
  const openIt = () => sheets.book.open(() => $("appt-new").setAttribute("aria-expanded", "false"));
  if (!moving) {
    $("appt-new").setAttribute("aria-expanded", "true");
    setTimeout(openIt, reducedMotion() ? 0 : 120);
  } else {
    openIt();
  }
}

const PART_OF_DAY = [["Morning", 0, 12], ["Afternoon", 12, 17], ["Evening", 17, 24]];
const surname = (name) => String(name || "").replace(/^Dr\.?\s*/i, "").trim().split(/\s+/).slice(-1)[0] || name;

async function findSlots() {
  showError($("book-error"), "");
  const params = new URLSearchParams({ service_id: $("book-service").value, day: $("book-day").value });
  if ($("book-branch").value) params.set("branch_id", $("book-branch").value);
  if ($("book-doctor").value) params.set("doctor_id", $("book-doctor").value);
  if (booking.mode === "move") params.set("ignore_appointment", booking.target.id);
  const box = $("book-slots");
  box.hidden = false;
  box.replaceChildren(loadingNode("Looking…", "span"));
  booking.slot = null;
  updateBookButton();
  try {
    const { slots } = await api(`/dashboard/api/slots?${params}`);
    if (!slots.length) {
      box.replaceChildren(el("span", { class: "ink-2 t-small", text: "No free times that day. Try another day or doctor." }));
      return;
    }
    const anyDoctor = !$("book-doctor").value;
    const pills = [];
    const pill = (slot, label) => {
      const button = el("button", {
        class: "slot", type: "button", "aria-pressed": "false", text: label,
        "aria-label": `${slot.time} with ${slot.doctor}, ${slot.branch}`, title: `${slot.doctor}, ${slot.branch}`,
      });
      button.addEventListener("click", () => {
        box.querySelectorAll("button.slot").forEach((b) => b.setAttribute("aria-pressed", "false"));
        button.setAttribute("aria-pressed", "true");
        booking.slot = slot;
        updateBookButton();
      });
      pills.push(button);
      return button;
    };
    // Grouped as Calendar's time picker would: morning, afternoon, evening. With any doctor,
    // one row per time with the free doctors beside it; with one doctor, just the times.
    const groups = PART_OF_DAY.map(([label, from, to]) => {
      const inPart = slots.filter((sl) => { const h = Number(sl.time.slice(0, 2)); return h >= from && h < to; });
      if (!inPart.length) return null;
      let content;
      if (anyDoctor) {
        const byTime = new Map();
        for (const sl of inPart) (byTime.get(sl.time) || byTime.set(sl.time, []).get(sl.time)).push(sl);
        content = el("div", { class: "slot-rows" }, ...[...byTime].map(([time, list]) => el("div", { class: "slot-row" },
          el("span", { class: "slot-time num", text: time }),
          el("div", { class: "slot-chips" }, ...list.map((sl) => pill(sl, `Dr ${surname(sl.doctor)}`))))));
      } else {
        content = el("div", { class: "slot-chips" }, ...inPart.map((sl) => pill(sl, sl.time)));
      }
      return el("section", { class: "slot-part" }, el("h3", { class: "slot-part-title", text: label }), content);
    }).filter(Boolean);
    box.replaceChildren(...groups);
    box.scrollTop = 0;
    // B5: the first free times pop in, 50ms apart; the rest are simply there.
    popIn(pills.slice(0, 12));
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
    sheets.book.close();
    if (currentTab === "appointments") loadAppointments();
    if (currentTab === "today") renderToday();
  } catch (err) {
    showError($("book-error"), err.message);
    if (err.status === 409) booking.key = newKey();   // a fresh attempt, not a replay of the failed one
    updateBookButton();
  }
});

// ------------------------------------------------------------------ tasks (Reminders)
let taskStatus = "open";
let taskBusyUntil = 0;          // our own tick is animating: an echo from the server waits

const TASK_ACTION = {
  callback: "Call back", emergency: "Emergency: call", red_flag: "Red flag: call", recovery_failed: "Find a new time with",
  escalation: "Call back", language: "Call back in their language:", abandoned: "Call back",
};
// "Call back Priya Sharma · 98450 12345": who to call, right in the title.
function taskTitle(t) {
  const who = [t.patient_name, t.phone_display].filter(Boolean).join(" · ");
  if (who) return `${TASK_ACTION[t.kind] || KIND_LABEL[t.kind] || "Call back"} ${who}`;
  return `${TASK_ACTION[t.kind] || "Call back"} the talk-page caller from ${fmtTime(t.created_at)}`;
}
const taskNote = (t) => t.note || (!t.phone_display ? "No number: the call came from the talk page." : "");

async function setTaskDone(t, done) {
  await api(`/dashboard/api/tasks/${t.id}`, { method: "POST", body: { done } });
  refreshSoon();
}

// A toast with one action (Undo), like Reminders after completing an item.
function actionToast(message, label, onAction) {
  const stack = $("toasts");
  stack.classList.add("toast-stack");
  const button = el("button", { type: "button", class: "toast-action", text: label });
  const node = el("div", { class: "toast-pill glass-pill", role: "status" }, el("span", { text: message }), button);
  const leave = () => {
    if (node.classList.contains("leaving")) return;
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 260);
  };
  button.addEventListener("click", () => { leave(); onAction(); });
  stack.append(node);
  setTimeout(leave, 6000);
}

async function loadTasks() {
  document.querySelectorAll("#task-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.status === taskStatus)));
  const list = $("task-rows");
  if (!list.querySelector(".reminder")) list.replaceChildren(loadingNode("Loading tasks…", "li"));
  let tasks;
  try {
    tasks = (await api(`/dashboard/api/tasks?status=${taskStatus}`)).tasks;
  } catch (err) {
    list.replaceChildren(emptyItem(err.message));
    return;
  }
  if (!tasks.length) {
    list.replaceChildren(emptyItem(taskStatus === "open" ? "Nothing waiting. Nice." : "No tasks."));
    return;
  }
  list.replaceChildren(...tasks.map((t) => {
    const done = t.status === "done";
    const box = el("input", { type: "checkbox", class: "check-circle",
      "aria-label": done ? `${taskTitle(t)}: done. Untick to reopen` : `Mark done: ${taskTitle(t)}` });
    box.checked = done;
    const item = el("li", { class: `reminder${done ? " done" : ""}${t.priority === "urgent" ? " urgent" : ""}` },
      box,
      el("div", { class: "rem-body" },
        el("div", { class: "rem-title" },
          !done && (t.priority === "urgent" || t.priority === "high") ? el("span", { class: "flag", text: t.priority === "urgent" ? "!!" : "!" }) : null,
          el("span", { class: "rem-text", text: taskTitle(t) })),
        taskNote(t) ? el("div", { class: "rem-note", text: taskNote(t) }) : null,
        !t.phone_display && t.note ? el("div", { class: "rem-note", text: "No number: the call came from the talk page." }) : null,
        el("div", { class: "rem-meta" },
          el("span", { text: `${KIND_LABEL[t.kind] || "Task"} · added ${ago(t.created_at)}`, title: fmtDateTime(t.created_at) }),
          !done && t.due_at ? el("span", { text: `Due ${fmtDateTime(t.due_at)}` }) : null,
          done ? el("span", { text: t.done_by ? `Done by ${t.done_by}` : "Done" }) : null)),
      el("div", { class: "rem-side" },
        !done && t.priority !== "normal" ? el("span", { class: `badge ${t.priority}`, text: t.priority === "urgent" ? "Urgent" : "High" }) : null,
        t.call_id ? el("button", { class: "btn-pill text small", type: "button", text: "Transcript",
          title: "Open the call this came from", onclick: () => { showTab("calls"); openCall(t.call_id); } }) : null));
    box.addEventListener("change", async () => {
      box.disabled = true;
      const nowDone = box.checked;
      const leaves = taskStatus !== "all";
      // Reminders: the circle fills (in CSS), then the row slides away and closes up.
      taskBusyUntil = Date.now() + 1200;
      item.classList.toggle("done", nowDone);
      try {
        await setTaskDone(t, nowDone);
      } catch (err) {
        toast(err.message, "error");
        box.checked = !nowDone;
        item.classList.toggle("done", !nowDone);
        box.disabled = false;
        return;
      }
      if (leaves) {
        setTimeout(() => {
          item.style.setProperty("--h", `${item.offsetHeight}px`);
          item.classList.add("leaving");
        }, reducedMotion() ? 0 : 420);
        setTimeout(loadTasks, reducedMotion() ? 220 : 900);
      } else {
        box.disabled = false;
      }
      actionToast(nowDone ? "Task done." : "Task reopened.", "Undo", async () => {
        try { await setTaskDone(t, !nowDone); } catch (err) { toast(err.message, "error"); }
        loadTasks();
      });
    });
    return item;
  }));
}
document.querySelectorAll("#task-filter button").forEach((button) => button.addEventListener("click", () => {
  taskStatus = button.dataset.status;
  loadTasks();
}));

// ------------------------------------------------------------------ calls (Mail)
let selectedCall = null;
let callList = [];
let callFilter = "all";

// Mail's mailbox filters: what a call came to, matched on its outcome and direction.
const CALL_FILTERS = {
  all: () => true,
  changes: (c) => /^(booked|rescheduled|cancelled)/.test(c.outcome || ""),
  hungup: (c) => /^abandoned|_hangup$|hung_up/.test(c.outcome || ""),
  recovery: (c) => c.direction === "outbound",
  live: (c) => !c.ended_at,
};

async function loadCalls() {
  document.querySelectorAll("#call-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === callFilter)));
  const list = $("call-rows");
  if (!list.querySelector(".mail-row")) list.replaceChildren(loadingNode("Loading calls…", "li"));
  try {
    callList = (await api("/dashboard/api/calls?limit=100")).calls;
  } catch (err) {
    list.replaceChildren(emptyItem(err.message));
    return;
  }
  renderCalls();
  if (selectedCall) openCall(selectedCall, { drill: $("view-calls").classList.contains("drilled") });
}

function renderCalls() {
  const list = $("call-rows");
  const q = $("call-search").value.trim().toLowerCase();
  const digits = q.replace(/\D/g, "");
  const calls = callList.filter(CALL_FILTERS[callFilter] || CALL_FILTERS.all).filter((c) => !q
    || c.id.toLowerCase().includes(q)
    || (digits.length >= 3 && (c.phone_display || "").replace(/\D/g, "").includes(digits))
    || callOutcome(c.outcome).toLowerCase().includes(q));
  $("call-count").textContent = `${calls.length} of ${callList.length}`;
  if (!calls.length) {
    list.replaceChildren(emptyItem(callList.length ? "No calls match." : "No calls yet."));
    return;
  }
  list.replaceChildren(...calls.map((c) => {
    const row = el("li", { tabindex: "0", role: "button", "data-call": c.id,
      class: `mail-row${c.id === selectedCall ? " selected" : ""}${c.ended_at ? "" : " live"}` },
    el("span", { class: "mail-who num", text: c.phone_display || `${CHANNEL_LABEL[c.channel] || "Unknown"} caller` }),
    el("span", { class: "mail-time", text: fmtDateTime(c.started_at) }),
    el("span", { class: "mail-outcome" },
      el("i", { class: `dot ${outcomeTone(c.outcome)}`, "aria-hidden": "true" }), callOutcome(c.outcome)),
    el("span", { class: "mail-sub" },
      el("span", { text: [c.direction === "outbound" ? "Recovery call" : null, CHANNEL_LABEL[c.channel] || null,
        c.ended_at ? length(c.started_at, c.ended_at) : "live",
        c.turns === c.kept_turns ? `${c.turns} line${c.turns === 1 ? "" : "s"}` : `${c.kept_turns} of ${c.turns} lines kept`].filter(Boolean).join(" · ") })));
    row.addEventListener("click", () => openCall(c.id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCall(c.id); } });
    return row;
  }));
}

document.querySelectorAll("#call-filter button").forEach((button) => button.addEventListener("click", () => {
  callFilter = button.dataset.filter;
  document.querySelectorAll("#call-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  renderCalls();
}));
$("call-search").addEventListener("input", debounce(renderCalls, 150));
// Mail: up and down arrows move through the list.
$("call-rows").addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  const rows = [...$("call-rows").querySelectorAll(".mail-row")];
  const i = rows.indexOf(document.activeElement);
  const next = rows[Math.max(0, Math.min(rows.length - 1, i + (event.key === "ArrowDown" ? 1 : -1)))];
  if (next) { event.preventDefault(); next.focus(); openCall(next.dataset.call); }
});

async function openCall(callId, { drill = true } = {}) {
  selectedCall = callId;
  // Opened from a task or a recovery call: clear a filter that would hide its row.
  if (!$("call-rows").querySelector(`[data-call="${CSS.escape(callId)}"]`) && (callFilter !== "all" || $("call-search").value)) {
    callFilter = "all";
    $("call-search").value = "";
    document.querySelectorAll("#call-filter button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === "all")));
    renderCalls();
  }

  const box = $("call-detail");
  // A different call: show the spinner if its transcript takes more than a moment.
  const shown = box.dataset.call;
  const slow = shown !== callId ? setTimeout(() => box.replaceChildren(loadingNode("Loading the call…")), 180) : null;
  let call;
  try {
    call = await api(`/dashboard/api/calls/${encodeURIComponent(callId)}`);
    clearTimeout(slow);
  } catch (err) {
    clearTimeout(slow);
    box.replaceChildren(el("p", { class: "field-error", text: err.message }));
    return;
  }
  if (selectedCall !== callId) return;       // another call was picked meanwhile
  box.dataset.call = callId;
  const kept = call.recording_consent !== 0;
  let prev = null;
  const turns = call.turns.map((t) => {
    const removed = t.text === null;
    const perceived = t.latency && t.latency.perceived_ms;
    const meta = t.role === "caller" ? "" : [t.tier !== null && t.tier !== undefined ? (TIER_LABEL[t.tier] || null) : null,
      perceived ? `replied in ${(perceived / 1000).toFixed(1)} s` : null,
      t.state_after && t.state_after !== t.state_before && GOAL_LABEL[t.state_after] ? `then ${GOAL_LABEL[t.state_after].toLowerCase()}` : null]
      .filter(Boolean).join(" · ");
    const node = el("div", { class: `line ${t.role}${removed ? " removed" : ""}${prev === t.role ? " same" : ""}` },
      el("span", { class: "who", text: ROLE_LABEL[t.role] || t.role }),
      el("div", { class: "bubble", text: removed ? "(text removed)" : t.text }),
      meta ? el("span", { class: "meta", text: meta }) : null);
    prev = t.role;
    return node;
  });
  const deleteButton = el("button", { class: "btn-pill small danger-text", type: "button", text: "Delete call data" });
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
  const back = el("button", { type: "button", class: "detail-back btn-pill text small", text: "‹ Calls",
    onclick: () => { $("view-calls").classList.remove("drilled"); const row = $("call-rows").querySelector(".mail-row.selected"); if (row) row.focus({ preventScroll: true }); } });
  box.replaceChildren(
    back,
    el("div", { class: "detail-head" },
      el("div", {},
        el("h2", { class: "t-section", text: call.phone_display || `${CHANNEL_LABEL[call.channel] || "Unknown"} caller` }),
        el("p", { class: "t-small ink-2", text: fmtDateTime(call.started_at) }))),
    el("div", { class: "detail-kv" }, el("dl", { class: "kv" },
      el("dt", { text: "Length" }), el("dd", { text: call.ended_at ? length(call.started_at, call.ended_at) : "Still on the call" }),
      el("dt", { text: "Direction" }), el("dd", { text: call.direction === "outbound" ? "Emma called them (recovery)" : "They called the clinic" }),
      el("dt", { text: "Outcome" }), el("dd", { text: callOutcome(call.outcome), title: call.outcome || "" }),
      el("dt", { text: "Came in by" }), el("dd", { text: CHANNEL_LABEL[call.channel] || "–" }),
      el("dt", { text: "Transcript" }), el("dd", { text: kept ? `kept until ${fmtDateTime(call.purge_after)}` : "not kept (caller's request or deleted)" }),
      call.appointments.length ? [el("dt", { text: "Appointments" }), el("dd", {},
        ...call.appointments.map((a) => el("div", { text: `${a.service}, ${fmtDateTime(a.start_utc)}, ${a.branch} (${STATUS_LABEL[a.status] || a.status})` })))] : null,
      call.tasks.length ? [el("dt", { text: "Tasks" }), el("dd", {},
        ...call.tasks.map((t) => el("div", { text: `${KIND_LABEL[t.kind] || t.kind} · ${t.priority} · ${t.status}` })))] : null)),
    el("div", { class: "transcript" }, ...(turns.length ? turns : [el("div", { class: "detail-empty", text: "Nothing was said on this call." })])),
    el("div", { class: "detail-foot" }, el("span", { class: "t-caption ink-2", text: `Call reference ${call.id}` }), deleteButton));
  box.scrollTop = 0;
  // On a phone the call opens as its own screen with a back button, as in Mail.
  if (drill && window.matchMedia("(max-width: 734px)").matches) {
    $("view-calls").classList.add("drilled");
    window.scrollTo({ top: 0 });
  }
  document.querySelectorAll("#call-rows .mail-row").forEach((row) => row.classList.toggle("selected", row.dataset.call === callId));
  const chosen = $("call-rows").querySelector(".mail-row.selected");
  if (chosen) chosen.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------ system (Settings)
$("privacy-export").addEventListener("click", async () => {
  const phone = $("privacy-phone").value.trim();
  try {
    const data = await api("/dashboard/api/privacy/export", { method: "POST", body: { phone } });
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `patient-data-${data.phone.slice(-4)}.json` });
    document.body.append(a); a.click(); a.remove();
    $("privacy-result").textContent = `Downloaded: ${data.appointments.length} appointments, ${data.calls.length} calls.`;
  } catch (err) { $("privacy-result").textContent = err.message; }
});
$("privacy-erase").addEventListener("click", async () => {
  const phone = $("privacy-phone").value.trim();
  const ok = await confirmAction("Erase this patient's data?",
    "Names, the number, call transcripts and messages for this number are removed. Past appointments stay as records without the name. This can't be undone.",
    "Erase");
  if (!ok) return;
  try {
    const out = await api("/dashboard/api/privacy/erase", { method: "POST", body: { phone } });
    $("privacy-result").textContent = `Erased: ${out.appointments} appointments and ${out.calls} calls de-identified.`;
    loadSystem();
  } catch (err) { $("privacy-result").textContent = err.message; }
});

let me = null;
async function loadMe() {
  try { me = await api("/dashboard/api/auth"); } catch { return null; }
  $("me-name").textContent = me.user ? `Signed in as ${me.user}` : "Staff dashboard";
  if (me.role === "demo") {
    // The resume login: say once that it's view-only, so a refused change isn't a surprise.
    $("me-name").textContent = `Signed in as ${me.user} · view only`;
    // A ribbon at the top says so for as long as they're signed in. It drops in the first
    // time and is calm after that.
    const ribbon = $("demo-ribbon");
    if (ribbon.hidden) {
      ribbon.classList.toggle("calm", Boolean(recall("demo-noted")));
      ribbon.hidden = false;
      store("demo-noted", "1");
    }
  }
  return me;
}

async function loadStaff() {
  const who = await loadMe();
  if (!who) return;
  $("staff-card").hidden = who.role !== "admin";
  buildSettingsIndex();
  if (who.role !== "admin") return;
  $("staff-me").textContent = `You're signed in as ${who.user}.`;
  let data;
  try { data = await api("/dashboard/api/staff"); } catch (err) { $("staff-result").textContent = err.message; return; }
  $("staff-rows").replaceChildren(...(data.staff.length ? data.staff.map((s) => el("li", { class: s.active ? "" : "muted" },
    el("span", { class: "li-main" }, el("span", { text: s.display_name || s.username }),
      el("span", { class: "li-sub" }, el("span", { class: "mono", text: s.username }),
        ` · ${s.role} · last sign-in ${s.last_login_at ? fmtDateTime(s.last_login_at) : "never"}`)),
    el("button", { class: `btn-pill small ${s.active ? "danger-text" : "gray"}`, type: "button",
      text: s.active ? "Switch off" : "Switch on",
      onclick: async () => {
        try {
          await api(`/dashboard/api/staff/${encodeURIComponent(s.username)}/active`, { method: "POST", body: { active: !s.active } });
          loadStaff();
        } catch (err) { toast(err.message, "error"); }
      } }))) : [emptyItem("Only the clinic password so far. Add a login for each person.")]));
}
$("staff-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/dashboard/api/staff", { method: "POST", body: {
      username: $("staff-username").value, display_name: $("staff-name").value,
      password: $("staff-password").value, role: $("staff-role").value } });
    $("staff-result").textContent = `Added ${$("staff-username").value}. They sign in with that user name and password.`;
    $("staff-form").reset();
    loadStaff();
  } catch (err) { $("staff-result").textContent = err.message; }
});

// Doctors and their weekly hours (clinic_admin.py). Admins edit; everyone can see.
async function loadDoctors() {
  let data, who;
  try {
    [data, who] = await Promise.all([api("/dashboard/api/doctors"), api("/dashboard/api/auth")]);
  } catch (err) { $("doctor-result").textContent = err.message; return; }
  const isAdmin = who.role === "admin";
  const form = $("doctor-form");
  form.hidden = !isAdmin;
  $("doctors-note").textContent = `Emma offers only these hours (clinic day ${data.day_hours.join("–")})`;
  if (isAdmin && !$("doctor-branch").options.length) {
    for (const b of data.branches) $("doctor-branch").append(el("option", { value: b, text: b }));
    $("doctor-services").replaceChildren(...data.services.map((s) => el("label", { class: "check" },
      el("input", { type: "checkbox", value: s }), ` ${s}`)));
  }
  const hours = (rota) => data.days.filter((d) => rota[d] !== "off").map((d) => `${d} ${rota[d]}`).join("; ") || "no hours";
  const rows = [];
  for (const d of data.doctors) {
    const row = el("tr", { class: d.active ? "" : "muted-row" },
      el("td", { text: d.spoken_name + (d.active ? "" : " (off)"), title: d.name }),
      el("td", { text: d.branch }),
      el("td", { class: "t-caption", text: d.services.join(", ") }),
      el("td", { class: "t-caption", text: hours(d.rota) }),
      el("td", { class: "actions" }, ...(isAdmin ? [
        el("button", { class: "btn-pill small gray", type: "button", text: "Edit", onclick: () => editDoctor(d, row, data) }),
        el("button", { class: "btn-pill small text", type: "button", text: d.active ? "Switch off" : "Switch on",
          onclick: async () => {
            try {
              await api(`/dashboard/api/doctors/${d.id}/active`, { method: "POST", body: { active: !d.active } });
              loadDoctors();
            } catch (err) { toast(err.message, "error"); }
          } })] : [])));
    rows.push(row);
  }
  $("doctor-rows").replaceChildren(...(rows.length ? rows : [emptyRow(5, "No doctors yet.")]));
}

function editDoctor(d, row, data) {
  if (row.nextSibling && row.nextSibling.classList && row.nextSibling.classList.contains("editor-row")) return;
  const inputs = Object.fromEntries(data.days.map((day) => [day, el("input", { type: "text", value: d.rota[day],
    "aria-label": `${d.spoken_name}, ${day}` })]));
  const services = data.services.map((s) => el("label", { class: "check" },
    el("input", { type: "checkbox", value: s, ...(d.services.includes(s) ? { checked: "" } : {}) }), ` ${s}`));
  const note = el("p", { class: "ink-2 t-caption", text: "Shifts like 09:00-13:00, 16:00-20:00, or off." });
  const editor = el("tr", { class: "editor-row" }, el("td", { colspan: "5" },
    el("div", { class: "rota-grid" }, ...data.days.map((day) => el("label", {}, el("span", { text: day }), inputs[day]))),
    el("div", { class: "checks" }, ...services),
    note,
    el("div", { class: "pop-actions" },
      el("button", { class: "btn-pill small", type: "button", text: "Save", onclick: async () => {
        try {
          const rota = Object.fromEntries(data.days.map((day) => [day, inputs[day].value]));
          const chosen = services.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
          const out = await api(`/dashboard/api/doctors/${d.id}/rota`, { method: "POST", body: { rota } });
          if (chosen.join("|") !== d.services.join("|")) {
            await api(`/dashboard/api/doctors/${d.id}/services`, { method: "POST", body: { services: chosen } });
          }
          $("doctor-result").textContent = out.affected.length
            ? `Saved. ${out.affected.length} upcoming booking(s) with ${d.spoken_name} are now outside these hours: block the doctor for those days on the Recovery tab to have Emma move them.`
            : `Saved. Emma offers ${d.spoken_name}'s new hours from the next call.`;
          loadDoctors();
        } catch (err) { note.textContent = err.message; }
      } }),
      el("button", { class: "btn-pill small gray", type: "button", text: "Cancel", onclick: () => editor.remove() }))));
  row.after(editor);
}

$("doctor-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const week = $("doctor-hours").value.trim() || "off";
  const rota = { Mon: week, Tue: week, Wed: week, Thu: week, Fri: week, Sat: week, Sun: "off" };
  const services = [...$("doctor-services").querySelectorAll("input:checked")].map((i) => i.value);
  try {
    const out = await api("/dashboard/api/doctors", { method: "POST", body: {
      name: $("doctor-name").value, branch: $("doctor-branch").value, gender: $("doctor-gender").value,
      services, rota } });
    $("doctor-result").textContent = `Added ${out.spoken_name}. Emma can book them from the next call; adjust single days with Edit.`;
    $("doctor-form").reset();
    loadDoctors();
  } catch (err) { $("doctor-result").textContent = err.message; }
});

async function loadClosures() {
  let data;
  try { data = await api("/dashboard/api/closures"); } catch (err) { $("closure-result").textContent = err.message; return; }
  const select = $("closure-branch");
  if (select.options.length === 1 && catalog && catalog.branches) {
    for (const b of catalog.branches) select.append(el("option", { value: b.name, text: b.name }));
  }
  $("closure-rows").replaceChildren(...(data.closures.length ? data.closures.map((c) => el("li", {},
    el("span", { class: "li-main" }, el("span", { text: prettyDay(c.date) }),
      el("span", { class: "li-sub", text: `${c.branch || "All branches"}${c.reason ? ` · ${c.reason}` : ""}` })),
    el("button", { class: "btn-pill small danger-text", type: "button", text: "Remove",
      onclick: async () => {
        try { await api(`/dashboard/api/closures/${c.id}/delete`, { method: "POST", body: {} }); loadClosures(); }
        catch (err) { toast(err.message, "error"); }
      } }))) : [emptyItem("No closures coming up.")]));
}
$("closure-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const out = await api("/dashboard/api/closures", { method: "POST", body: {
      date: $("closure-date").value, branch: $("closure-branch").value, reason: $("closure-reason").value } });
    $("closure-result").textContent = out.affected.length
      ? `Added. ${out.affected.length} appointment(s) already booked that day: block the doctors on the Recovery tab to have Emma call them.`
      : "Added.";
    loadClosures();
  } catch (err) { $("closure-result").textContent = err.message; }
});

async function loadPerformance() {
  const box = $("perf-body");
  let data;
  try {
    data = await api("/dashboard/api/performance?days=7");
  } catch (err) {
    box.textContent = err.message;
    return;
  }
  const channels = Object.entries(data.channels);
  if (!channels.length) {
    box.replaceChildren(el("p", { class: "ink-2 pad", text: "No calls in the last 7 days." }));
    return;
  }
  const sec = (v) => (v === null || v === undefined ? null : `${(v / 1000).toFixed(1)} s`);
  const pct = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : null);
  const pair = (a, b) => (sec(a) && sec(b) ? `${sec(a)} / ${sec(b)}` : "No data yet");
  const row = (label, f) => el("tr", {}, el("th", { text: label }), ...channels.map(([, c]) => el("td", { class: "num", text: f(c) })));
  box.replaceChildren(el("div", { class: "table-wrap" }, el("table", { class: "table kv-table" },
    el("thead", {}, el("tr", {}, el("th", {}), ...channels.map(([name, c]) =>
      el("th", { text: `${CHANNEL_LABEL[name] || name} (${c.calls} call${c.calls === 1 ? "" : "s"} with replies)` })))),
    el("tbody", {},
      row("Replies Emma gave", (c) => String(c.replies)),
      row("Quick replies (no thinking needed)", (c) => (c.replies ? `${c.rules} (${pct(c.rules, c.replies)})` : "No data yet")),
      row("Replies that needed thinking", (c) => (c.replies ? `${c.model + c.fallback} (${pct(c.model + c.fallback, c.replies)})` : "No data yet")),
      row("Of those, thought through in time", (c) => (c.model + c.fallback ? `${c.model} (${pct(c.model, c.model + c.fallback)})` : "No data yet")),
      row("Of those, a backup reply because understanding was slow", (c) => (c.model + c.fallback
        ? `${c.fallback} (${pct(c.fallback, c.model + c.fallback)})` : "No data yet")),
      row("Time to a quick reply, usual / slowest", (c) => pair(c.simple_p50_ms, c.simple_p95_ms)),
      row("Time to a thought-through reply, usual / slowest", (c) => pair(c.model_p50_ms, c.model_p95_ms)),
      row("Of that, waiting for the caller to finish", (c) => sec(c.end_of_speech_p50_ms) || "No data yet"),
      row("Estimated cost per call (7-day total)", (c) => (c.metered_calls
        ? `$${c.cost_per_call_usd.toFixed(3)} ($${c.cost_total_usd.toFixed(2)} over ${c.metered_calls} costed call${c.metered_calls === 1 ? "" : "s"}, including ones that ended before a reply)`
        : "No data yet"))))));
}

async function loadSystem() {
  loadPerformance();
  loadClosures();
  loadDoctors();
  loadStaff();
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
    el("td", { class: "t-caption", text: o.last_error || "–" }),
    el("td", { class: "nowrap", text: fmtDateTime(o.due_at) }),
    el("td", { class: "actions" }, el("button", {
      class: "btn-pill small gray", type: "button", text: "Retry",
      onclick: async () => {
        try {
          await api(`/dashboard/api/sync/${encodeURIComponent(o.appointment_id)}/retry`, { method: "POST", body: {} });
          toast("Queued for another try.");
          loadSystem();
        } catch (err) { toast(err.message, "error"); }
      },
    }))));
  $("sync-rows").replaceChildren(...(rows.length ? rows : [emptyRow(8, "Everything is in sync.")]));

  const dnc = data.do_not_call.map((d) => el("li", {},
    el("span", { class: "li-main" }, el("span", { class: "num", text: d.phone_display }),
      el("span", { class: "li-sub", text: `Added ${fmtDateTime(d.updated_at)}` })),
    el("button", {
      class: "btn-pill small danger-text", type: "button", text: "Remove",
      onclick: async () => {
        try {
          await api("/dashboard/api/dnc/remove", { method: "POST", body: { phone: d.phone_e164 } });
          loadSystem();
        } catch (err) { toast(err.message, "error"); }
      },
    })));
  $("dnc-rows").replaceChildren(...(dnc.length ? dnc : [emptyItem("Nobody on the list.")]));

  const msgStatus = data.message_status || {};
  const mc = data.message_counts || {};
  $("msg-state").className = `badge ${msgStatus.sender === "twilio" ? "ok" : "warn"}`;
  $("msg-state").textContent = msgStatus.sender === "twilio" ? `Sending (${msgStatus.channel})` : msgStatus.sender === "off" ? "Off" : "Log only";
  $("msg-detail").textContent = [msgStatus.reason ? `${msgStatus.reason}.` : null,
    `${mc.pending || 0} waiting, ${mc.sent || 0} sent, ${mc.logged || 0} logged, ${mc.failed || 0} failed.`]
    .filter(Boolean).join(" ");
  const MSG_KIND = { booked: "Confirmation", moved: "Moved", cancelled: "Cancelled", reminder: "Reminder" };
  $("msg-rows").replaceChildren(...((data.messages || []).length ? data.messages.map((m) => el("tr", {},
    el("td", { text: MSG_KIND[m.kind] || m.kind }),
    el("td", { class: "num nowrap", text: m.phone_display }),
    el("td", { class: "t-caption", text: m.body }),
    el("td", {}, el("span", { class: `badge ${m.status}`, title: m.last_error || "", text: m.status })),
    el("td", { class: "nowrap", text: fmtDateTime(m.sent_at || m.due_at) }))) : [emptyRow(5, "No messages yet.")]));

  $("audit-rows").replaceChildren(...data.audit.map((a) => el("tr", {},
    el("td", { class: "nowrap", text: fmtDateTime(a.ts) }),
    el("td", { text: a.actor }),
    el("td", { text: a.action.replace(/_/g, " ") }),
    el("td", { class: "t-caption", title: `${a.entity} ${a.entity_id}`, text: a.what || `${a.entity} ${a.entity_id}` }))));
  loadHealth(data);
  buildSettingsIndex();
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

// Settings: an index of the groups on the left, one group's pane at a time on the right.
// On a phone the index is a list you drill into, with a back button, as in iOS Settings.
let settingsPane = recall("settings-pane");
function buildSettingsIndex() {
  const index = $("settings-index");
  const groups = [...document.querySelectorAll("#settings-panes > .settings-group")];
  groups.forEach((group, i) => { if (!group.id) group.id = `settings-${i}`; });
  const visible = groups.filter((g) => !g.hidden);
  if (!visible.some((g) => g.id === settingsPane)) settingsPane = visible[0] && visible[0].id;
  index.replaceChildren(...visible.map((group) => {
    const title = group.querySelector(".group-title");
    const label = title ? title.childNodes[0].textContent.trim() : group.id;
    const badge = group.querySelector(".group-title .badge");
    return el("button", { type: "button", class: "settings-row", "aria-current": String(group.id === settingsPane),
      "data-pane": group.id, onclick: () => showSettingsPane(group.id, true) },
    el("span", { class: "li-main", text: label }),
    badge && badge.textContent !== "–" ? el("span", { class: badge.className, text: badge.textContent }) : null,
    el("span", { class: "chev", "aria-hidden": "true", text: "›" }));
  }));
  showSettingsPane(settingsPane, $("view-system").classList.contains("drilled"));
}
function showSettingsPane(id, drill) {
  settingsPane = id;
  store("settings-pane", id);
  document.querySelectorAll("#settings-panes > .settings-group").forEach((g) => g.classList.toggle("active", g.id === id));
  document.querySelectorAll("#settings-index .settings-row").forEach((r) => r.setAttribute("aria-current", String(r.dataset.pane === id)));
  $("view-system").classList.toggle("drilled", Boolean(drill));
  if (drill && window.matchMedia("(max-width: 734px)").matches) window.scrollTo({ top: 0 });
}
document.querySelectorAll("#settings-panes > .settings-group").forEach((group) => {
  const back = el("button", { type: "button", class: "settings-back btn-pill text small", text: "‹ System",
    onclick: () => $("view-system").classList.remove("drilled") });
  group.prepend(back);
});

// ------------------------------------------------------------------ recovery calls (guided steps)
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
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => p.find((x) => x.type === type).value;
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
  const list = $("block-rows");
  if (!blocks.length) {
    list.replaceChildren(emptyItem("No doctor is blocked."));
    return;
  }
  list.replaceChildren(...blocks.map((b) => el("li", { class: b.id === recovery.blockId ? "selected" : "" },
    el("span", { class: "li-main" }, el("span", { text: `${b.doctor} (${b.branch})` }),
      el("span", { class: "li-sub", text: `${fmtDateTime(b.start)} to ${fmtDateTime(b.end)} · ${b.reason_category}` })),
    el("button", { class: "btn-pill small gray", type: "button", text: "Preview", onclick: () => showPreview(b.id) }),
    el("button", {
      class: "btn-pill small danger-text", type: "button", text: "Lift",
      onclick: async () => {
        const text = `${b.doctor} becomes bookable again. Calls still waiting for this block stop; appointments already moved stay moved.`;
        if (!await confirmAction("Lift this block?", text, "Lift block")) return;
        try {
          await api(`/dashboard/api/recovery/blocks/${b.id}/lift`, { method: "POST", body: {} });
          loadRecovery();
        } catch (err) { toast(err.message, "error"); }
      },
    }))));
}

async function showPreview(blockId, keepTicks = false) {
  recovery.blockId = blockId;
  document.querySelectorAll("#block-rows li").forEach((li) => li.classList.remove("selected"));
  try {
    renderPreview(await api(`/dashboard/api/recovery/blocks/${blockId}/preview`), keepTicks);
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderPreview(preview, keepTicks = false) {
  const before = new Set([...document.querySelectorAll("#preview-rows input:checked")].map((b) => b.value));
  recovery.preview = preview;
  const list = $("preview-rows");
  if (!preview) {
    $("preview-summary").textContent = "Block a doctor or pick a block to see who is affected.";
    list.replaceChildren();
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
      rows.push(el("li", {},
        box,
        el("span", { class: "li-main" },
          el("span", {}, a.patient_name, g.do_not_call && i === 0 ? el("span", { class: "badge bad", text: "do not call" }) : null),
          el("span", { class: "li-sub", text: `${fmtDateTime(a.start)} · ${a.service}` })),
        el("span", { class: "num ink-2 t-small", text: i === 0 ? g.phone_masked : "" })));
    });
  }
  list.replaceChildren(...(rows.length ? rows : [emptyItem("Nobody to call.")]));
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
    badge.className = "state-pill";
    detail.textContent = "";
  } else if (runner.ringing) {
    badge.textContent = "Ringing";
    badge.className = "state-pill warn";
    detail.textContent = `Calling ${runner.ringing.to_name || "the patient"} (${runner.ringing.to_masked}). Answer on the patient page.`;
  } else if (runner.paused) {
    badge.textContent = "Paused";
    badge.className = "state-pill warn";
    detail.textContent = `Waiting: ${runner.paused}.`;
  } else if (runner.waiting && runner.waiting.queued) {
    const w = runner.waiting;
    badge.textContent = "Waiting";
    badge.className = "state-pill info";
    detail.textContent = `${w.queued} call(s) queued` + (w.next_retry ? `; next try ${fmtDateTime(w.next_retry)}` : "")
      + (w.paused_campaigns ? `; ${w.paused_campaigns} campaign(s) paused` : "")
      + `. Calls ring only ${runner.window} clinic time and inside each patient's own hours.`;
  } else {
    badge.textContent = "Ready";
    badge.className = "state-pill ok";
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
    class: `btn-pill small ${cls}`, type: "button", text: label,
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
  const bits = [];
  if (a.moved) bits.push(`${a.moved} moved`);
  if (a.cancelled) bits.push(`${a.cancelled} cancelled`);
  if (a.needs_reschedule) bits.push(`${a.needs_reschedule} need a new time`);
  if (k.retrying) bits.push(`${k.retrying} call${k.retrying === 1 ? "" : "s"} to retry`);
  if (k.waiting) bits.push(`${k.waiting} waiting`);
  if (k.skipped) bits.push(`${k.skipped} skipped`);
  return bits.length ? bits.join(" · ") : "No results yet.";
}

function jobCallLabel(j) {
  if (j.status === "queued" && j.next_attempt_at) {
    return `Retry ${fmtDateTime(j.next_attempt_at)} (try ${j.attempts + 1} of ${Math.max(j.max_attempts, j.attempts + 1)})`;
  }
  return JOB_LABEL[j.status] || j.status;
}

// B10: one dot per patient, coloured by how their call went; the patient being called now is
// a 48px pill whose fill runs while the phone rings. Long campaigns keep the ring alone.
const JOB_DOT = { done: "done", failed: "failed", skipped: "skipped", ringing: "now", in_call: "now" };
function jobMeter(jobs) {
  if (!jobs.length || jobs.length > 24) return null;
  const meter = el("div", { class: "e-meter job-meter", role: "img",
    "aria-label": jobs.map((j) => `${j.name || "Patient"}: ${jobCallLabel(j)}`).join("; ") });
  for (const j of jobs) {
    const kind = JOB_DOT[j.status] || "queued";
    const dot = el("i", { class: `job-${kind}${kind === "now" ? " is-current" : ""}`, title: `${j.name || "Patient"}: ${jobCallLabel(j)}` });
    if (kind === "now") dot.style.setProperty("--w", "48px");
    meter.append(dot);
  }
  return meter;
}

function renderCampaigns(campaigns) {
  const box = $("campaign-list");
  if (!campaigns.length) {
    box.replaceChildren(el("p", { class: "ink-2 t-small", text: "No recovery calls yet." }));
    return;
  }
  box.replaceChildren(...campaigns.map((c) => {
    const running = c.status === "running";
    const paused = running && Boolean(c.paused_at);
    const controls = running ? [
      paused ? campaignButton(c, "resume", "Resume", "") : campaignButton(c, "pause", "Pause", "gray"),
      campaignButton(c, "stop", "Stop", "danger-text", "Calls still waiting won't be made. A call in progress finishes."),
    ] : [];
    const total = c.jobs.length || 1;
    const finished = c.jobs.filter((j) => ["done", "failed", "skipped"].includes(j.status)).length;
    const ring = el("div", { class: "ring", "data-label": `${finished}/${c.jobs.length}` });
    ring.style.setProperty("--p", String(Math.round((100 * finished) / total)));
    const head = el("div", { class: "campaign-head" },
      el("h3", { text: `${c.doctor} · started ${fmtDateTime(c.created_at)}` }),
      el("span", {
        class: `badge ${paused ? "warn" : running ? "warn" : c.status === "completed" ? "ok" : ""}`,
        text: paused ? "paused" : c.status,
      }),
      ...controls,
      el("a", { class: "btn-pill small text", href: `/dashboard/api/recovery/campaigns/${c.id}.csv`, text: "Export CSV" }));
    const rows = c.jobs.map((j) => el("tr", {},
      el("td", { text: j.name || "–" }),
      el("td", { class: "num nowrap", text: j.phone_masked }),
      el("td", {}, ...j.appointments.map((a) => el("div", { class: "nowrap" },
        `${fmtDateTime(a.start)} · ${a.service} · ${a.doctor} `,
        el("span", { class: `badge ${a.status}`, text: STATUS_LABEL[a.status] || a.status })))),
      el("td", {}, el("span", { class: `badge ${jobBadge(j.status)}`, text: jobCallLabel(j) })),
      el("td", { text: j.outcome ? (OUTCOME_LABEL[j.outcome] || j.outcome) : "–" }),
      el("td", {}, j.call_id && ["done", "failed"].includes(j.status) ? el("button", {
        class: "btn-pill small text", type: "button", text: "Transcript", title: j.call_id,
        onclick: () => { showTab("calls"); openCall(j.call_id); },
      }) : "–")));
    const header = ["Patient", "Phone", "Appointments", "Call", "Result", ""].map((h) => el("th", { text: h }));
    return el("div", { class: "campaign" }, head,
      el("div", { class: "campaign-progress" }, ring,
        el("div", { class: "campaign-text" }, jobMeter(c.jobs), el("p", { class: "ink-2 t-small", text: summaryText(c) }))),
      el("div", { class: "table-wrap" }, el("table", { class: "table" },
        el("thead", {}, el("tr", {}, ...header)), el("tbody", {}, ...rows))));
  }));
}

function renderWindows(windows) {
  const list = $("window-rows");
  if (!windows.length) {
    list.replaceChildren(emptyItem("No patient has their own calling hours."));
    return;
  }
  list.replaceChildren(...windows.map((w) => el("li", {},
    el("span", { class: "li-main num", text: w.phone_masked }),
    el("span", { class: "ink-2", text: `after ${w.call_after || "–"} · before ${w.call_before || "–"}` }))));
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

async function logout() {
  try { await fetch("/dashboard/api/logout", { method: "POST", credentials: "same-origin" }); } finally {
    location.replace("/dashboard/login");
  }
}
$("logout").addEventListener("click", logout);
document.querySelector('#more-sheet [data-action="logout"]').addEventListener("click", logout);

// B16: on first load the sidebar's rows slide in once, one after another.
function sidebarEntrance() {
  const side = $("sidebar");
  // Once per sign-in, not on every reload of a tool used all day.
  let seen = false;
  try { seen = sessionStorage.getItem("emma-dashboard:entered") === "1"; sessionStorage.setItem("emma-dashboard:entered", "1"); } catch { seen = true; }
  if (seen || reducedMotion() || getComputedStyle(side).display === "none") return;
  const rows = [side.querySelector(".side-brand"), ...side.querySelectorAll(".side-nav > .side-item"), side.querySelector(".side-foot")];
  rows.forEach((row, i) => row.style.setProperty("--i", String(i)));
  side.classList.add("is-entering");
  setTimeout(() => side.classList.remove("is-entering"), 350 + rows.length * 40 + 80);
}

// Mark the section the URL asks for straight away, before any data loads, so two rows are
// never selected at once on the first paint.
function selectNav(name) {
  if (!loaders[name]) name = "today";
  document.querySelectorAll(".side-item[data-tab]").forEach((item) => item.setAttribute("aria-selected", String(item.dataset.tab === name)));
  document.querySelectorAll(".tab-item").forEach((item) => item.setAttribute("aria-selected", String(item.dataset.tab === (TAB_FOR[name] || name))));
}

// Icon-only rail: each row keeps its name for screen readers and as a tooltip.
function nameNavItems() {
  document.querySelectorAll(".side-item, .tab-item").forEach((item) => {
    const label = item.querySelector(".side-label, span:not(.tab-dot):not(.tab-badge)");
    if (label && label.textContent.trim()) {
      item.setAttribute("aria-label", label.textContent.trim());
      if (item.classList.contains("side-item")) item.title = label.textContent.trim();
    }
  });
}

async function start() {
  selectNav(location.hash.slice(1) || recall("tab") || "today");
  nameNavItems();
  sidebarEntrance();
  // B4: segmented controls with a sliding indicator. The page sets aria-pressed itself.
  for (const id of ["cal-view", "task-filter", "appt-scope", "call-filter"]) segmentPlacers.push(tabnav($(id), { manage: false }));
  // B17: disclosures open and close with a height change and a turning chevron.
  accordion($("live-timings"));
  accordion($("window-acc"));
  await refreshOverview();
  try { await loadCatalog(); } catch (err) { toast(err.message, "error"); }
  loadMe();
  connectEvents();
  const wanted = location.hash.slice(1) || recall("tab") || "today";
  currentTab = "";
  showTab(wanted);
  reveal();
  cardHover();
  setInterval(refreshOverview, 30000);
  setInterval(() => { if (currentTab === "appointments" && ["day", "week"].includes(appt.view)) renderAppointments(); }, 60000);
}

start();
