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
    throw new ApiError(res.status, "Please sign in.");
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

// ------------------------------------------------------------------ motion (docs/REDESIGN_PLAN.md, Motion)
// A critically damped spring (damping 1.0) as a CSS linear() easing: x(t) = 1 - (1 + wt)e^(-wt), w = 2pi/response.
// It has no overshoot, and it settles when the remaining distance is under 0.1%.
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)");

function springEasing(response) {
  const w = (2 * Math.PI) / response;
  const settle = 9.23 / w;
  const points = [];
  for (let i = 0; i <= 24; i++) {
    const t = (settle * i) / 24;
    points.push((1 - (1 + w * t) * Math.exp(-w * t)).toFixed(4));
  }
  points[points.length - 1] = "1";
  return { easing: `linear(${points.join(", ")})`, duration: Math.round(settle * 1000) };
}
const SPRING = springEasing(0.3);
document.documentElement.style.setProperty("--spring", SPRING.easing);

// Sheets and popovers grow from the control that opened them and return the same way (spatial consistency).
let lastTrigger = null;
document.addEventListener("pointerdown", (e) => { lastTrigger = e.target.closest("button, a, [tabindex]"); }, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") lastTrigger = document.activeElement;
}, true);

function originFrom(node, trigger) {
  if (!trigger || !trigger.isConnected) return "50% 50%";
  const r = node.getBoundingClientRect();
  const t = trigger.getBoundingClientRect();
  return `${t.left + t.width / 2 - r.left}px ${t.top + t.height / 2 - r.top}px`;
}

function grow(node, trigger) {
  if (REDUCED_MOTION.matches) {
    node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 150 });
    return;
  }
  node.style.transformOrigin = originFrom(node, trigger);
  node.animate([{ opacity: 0, transform: "scale(0.88)" }, { opacity: 1, transform: "none" }], SPRING);
}

function present(dialog) {
  const trigger = lastTrigger;
  dialog.showModal();
  dialog._trigger = trigger;
  grow(dialog, trigger);
}

function dismiss(dialog, value = "") {
  if (!dialog.open || dialog._closing) return;
  dialog._closing = true;
  const done = () => { dialog._closing = false; dialog.close(value); };
  if (REDUCED_MOTION.matches) { done(); return; }
  dialog.style.transformOrigin = originFrom(dialog, dialog._trigger);
  const shrink = dialog.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(0.88)" }],
    { duration: 180, easing: "cubic-bezier(0.4, 0, 1, 1)" });
  shrink.onfinish = done;
  shrink.oncancel = done;
}

// Popovers are placed next to the control that opened them, kept inside the window.
function place(popover, anchor, align = "end") {
  const r = anchor.getBoundingClientRect();
  const pw = popover.offsetWidth;
  const ph = popover.offsetHeight;
  let left = align === "start" ? r.left : r.right - pw;
  left = Math.max(8, Math.min(left, innerWidth - pw - 8));
  let top = r.bottom + 6;
  if (top + ph > innerHeight - 8) top = Math.max(8, r.top - ph - 6);
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

document.querySelectorAll("[popovertarget]").forEach((button) => {
  const popover = $(button.getAttribute("popovertarget"));
  popover.addEventListener("toggle", (e) => {
    if (e.newState !== "open") return;
    place(popover, button, "end");
    grow(popover, button);
  });
});

function openMenu(anchor, items) {
  const menu = $("row-menu");
  menu.replaceChildren(...items.map((item) => el("button", {
    type: "button", role: "menuitem", class: item.danger ? "danger" : "", text: item.label,
    onclick: () => { menu.hidePopover(); item.run(); },
  })));
  menu.showPopover();
  place(menu, anchor);
  grow(menu, anchor);
  menu.querySelector("button")?.focus();
}
$("row-menu").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  const items = [...$("row-menu").querySelectorAll("button")];
  const i = items.indexOf(document.activeElement);
  items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
});

function toast(message, kind = "", action = null) {
  const node = el("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" },
    el("span", { text: message }),
    action ? el("button", { type: "button", text: action.label, onclick: () => { node.remove(); action.run(); } }) : null);
  $("toasts").append(node);
  if (!REDUCED_MOTION.matches) {
    node.animate([{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "none" }], SPRING);
  }
  setTimeout(() => {
    if (!node.isConnected) return;
    if (REDUCED_MOTION.matches) { node.remove(); return; }
    // Leaves the way it came.
    node.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(12px)" }],
      { duration: 200, easing: "cubic-bezier(0.4, 0, 1, 1)" }).onfinish = () => node.remove();
  }, kind === "urgent" ? 12000 : action ? 7000 : 4500);
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

function emptyItem(text) {
  return el("li", { class: "empty", text });
}

const STATUS_LABEL = {
  booked: "Booked", cancelled: "Cancelled", needs_reschedule: "Needs reschedule",
  completed: "Completed", no_show: "No-show",
};
const KIND_LABEL = {
  callback: "Callback", emergency: "Emergency", red_flag: "Red flag", recovery_failed: "Rebooking call failed",
  escalation: "Escalation", language: "Language", abandoned: "Abandoned call",
};
const ROLE_LABEL = { caller: "Caller", emma: "Emma", operator: "Staff" };
const TIER_LABEL = { "-1": "greeting", 0: "Emma's rules", 1: "language model", 2: "fallback (model slow)" };
const CHANNEL_LABEL = { browser: "Browser", phone: "Phone" };
// Where a caller hung up, by the step Emma was on.
const STEP_LABEL = {
  greet: "at the greeting", go_on: "after the greeting", ask_name: "while giving their name",
  spell_name: "while spelling their name", confirm_phone: "while confirming their number",
  ask_phone: "while giving their number", ask_service: "while saying what it's for", ask_branch: "choosing a branch",
  ask_when: "choosing a day", ask_time: "choosing a time", offer_slots: "choosing a slot", summary: "at the summary",
  duplicate_check: "about an existing appointment", callback_offer: "after a callback offer",
};
const CALL_OUTCOME = {
  booked: "Booked", rescheduled: "Moved", cancelled: "Cancelled", completed: "Completed", transferred: "Transferred",
  booked_hangup: "Booked, hung up before hearing it", rescheduled_hangup: "Moved, hung up before hearing it",
  cancelled_hangup: "Cancelled, hung up before hearing it",
};

function callOutcome(outcome) {
  if (!outcome) return "In progress";
  if (CALL_OUTCOME[outcome]) return CALL_OUTCOME[outcome];
  const [kind, step] = outcome.split("@");
  if (kind === "abandoned") return `Hung up ${STEP_LABEL[step] || (step ? `at ${step.replace(/_/g, " ")}` : "")}`.trim();
  return outcome.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// ------------------------------------------------------------------ dialogs
document.querySelectorAll("dialog [data-close]").forEach((button) => {
  button.addEventListener("click", () => dismiss(button.closest("dialog")));
});
document.querySelectorAll("dialog").forEach((dialog) => {
  // Esc closes along the same path the sheet came in on.
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); dismiss(dialog); });
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
    dialog.addEventListener("close", done);
    present(dialog);
  });
}
$("dlg-confirm").querySelector("form").addEventListener("submit", (e) => {
  e.preventDefault();
  dismiss($("dlg-confirm"), "ok");
});

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

// Counts on the global nav: open tasks (Today), problems for Health (Clinic).
function setBadge(name, count, { urgent = false, label = "" } = {}) {
  document.querySelectorAll(`[data-badge="${name}"]`).forEach((badge) => {
    badge.hidden = !count;
    badge.textContent = String(count || "");
    badge.classList.toggle("urgent", urgent);
    if (label) badge.setAttribute("aria-label", label);
  });
}

async function refreshOverview() {
  let data;
  try { data = await api("/dashboard/api/overview"); } catch { return; }
  if (data.timezone) TZ = data.timezone;
  const openTasks = Object.values(data.tasks).reduce((a, b) => a + b, 0);
  const urgent = data.tasks.urgent || 0;
  setBadge("tasks", openTasks, {
    urgent: urgent > 0, label: urgent ? `${openTasks} open, ${urgent} urgent` : `${openTasks} open`,
  });
  const failed = data.sync.failed || 0;
  const alerts = data.alerts || [];
  const problems = alerts.length + (failed ? 1 : 0);
  setBadge("health", problems, { urgent: true, label: `${problems} problem${problems === 1 ? "" : "s"}` });
  updateCallChip(data.call);
  renderAlerts(alerts);
  // One line of live status under each link in the drop-down menus.
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  setStatus("appointments", `${data.appointments_today} today`
    + (data.needs_reschedule ? `, ${data.needs_reschedule} need${data.needs_reschedule === 1 ? "s" : ""} a new time` : ""));
  setStatus("tasks", openTasks ? `${openTasks} open${urgent ? `, ${urgent} urgent` : ""}` : "Nothing waiting");
  setStatus("calls", `${plural(data.calls_today, "call")} today`);
  setStatus("health", problems ? `${plural(problems, "problem")} to look at` : "Everything is working");
}

function setStatus(key, text) {
  document.querySelectorAll(`[data-status="${key}"]`).forEach((node) => { node.textContent = text; });
}

// Problems the server found (alerts.py): a speech service down, calendar sync stuck, a call line stuck.
function renderAlerts(alerts) {
  const box = $("alerts");
  box.hidden = alerts.length === 0;
  const icon = () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-alert");
    svg.append(use);
    return svg;
  };
  box.replaceChildren(...alerts.map((a) => el("p", { class: `alert-line ${a.level}` }, icon(),
    el("span", { text: `${a.text} (since ${fmtDateTime(a.since)})` }),
    currentTab === "health" ? null : el("a", { href: "#health", text: "Health ›" }))));
}

// Emma's pearl next to "Live call" glows while a call is on.
function updateCallChip(call) {
  const busy = Boolean(call && call.busy);
  document.querySelectorAll("[data-live-pearl]").forEach((pearl) => pearl.classList.toggle("live", busy));
  setStatus("live", busy ? "Emma is on a call" : "No call right now");
  const live = [...liveCalls.values()].filter((st) => !st.ended).length;
  setBadge("live", live > 1 ? live : 0, { label: `${live} calls on` });
}

// ------------------------------------------------------------------ sections and routing
// Each section has its own address (#appointments, …), so Back and Forward move between them.
const loaders = {
  live: () => renderLive(),
  appointments: () => loadAppointments(),
  tasks: () => loadTasks(),
  calls: () => loadCalls(),
  rebooking: () => loadRecovery(),
  health: () => loadHealthView(),
  setup: () => loadSetup(),
  privacy: () => loadPrivacy(),
};
const OLD_NAMES = { recovery: "rebooking", system: "health", clinic: "health" };
const GROUP_OF = {
  live: "today", appointments: "today", tasks: "today", calls: "followup", rebooking: "followup",
  health: "clinic", setup: "clinic", privacy: "clinic",
};
let currentTab = "live";

function sectionName(name) {
  name = OLD_NAMES[name] || name;
  return loaders[name] ? name : null;
}

function showTab(name) {
  name = sectionName(name) || "live";
  if (location.hash === `#${name}`) render(name);
  else location.hash = name;               // hashchange renders it and records it in history
}

function render(name) {
  const changed = name !== currentTab;
  currentTab = name;
  closeNav();
  for (const key of Object.keys(loaders)) $(`view-${key}`).hidden = key !== name;
  document.querySelectorAll("[data-nav]").forEach((link) => {
    if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.querySelectorAll(".gn-item[data-menu]").forEach((item) => item.classList.toggle("current", item.dataset.menu === GROUP_OF[name]));
  store("tab", name);
  const brand = document.querySelector(".gn-brand").textContent;
  document.title = `${$(`view-${name}`).querySelector("h1").textContent} · ${brand}`;
  if (changed) window.scrollTo(0, 0);
  refreshSoon();
  const loaded = Promise.resolve(loaders[name]()).catch(() => {});
  // apple.com's staggered reveal each time a section opens (owner's choice); refreshes don't re-run it.
  if (changed) loaded.then(() => requestAnimationFrame(() => reveal($(`view-${name}`))));
}

// ------------------------------------------------------------------ apple.com motion: reveal on scroll (F8)
// Items fade in (0.9 s) and rise 30 px (0.7 s), 0.15 s apart, when their top reaches 70% of the
// viewport. apple.com sets the curve in script, so its content curve stands in (docs/REDESIGN_V2_APPLE.md F9).
const revealer = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  let i = 0;
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const node = entry.target;
    revealer.unobserve(node);
    node.style.setProperty("--reveal-delay", `${Math.min(i++, 4) * 0.15}s`);
    node.classList.add("revealed");
    const settle = () => { node.classList.remove("reveal-item", "revealed"); node.style.removeProperty("--reveal-delay"); };
    node.addEventListener("transitionend", (e) => { if (e.propertyName === "opacity") settle(); }, { once: true });
    setTimeout(settle, 2000);
  }
}, { rootMargin: "0px 0px -30% 0px" }) : null;

function reveal(view) {
  if (!revealer || REDUCED_MOTION.matches || view.hidden) return;
  const items = view.querySelectorAll(
    ".view-body > .view-intro, .view-body > .toolbar, .view-body .card:not(.list-card), .view-body .list-card > .list > li");
  for (const node of items) {
    if (node.classList.contains("reveal-item")) continue;
    node.classList.add("reveal-item");
    revealer.observe(node);
  }
}

// ------------------------------------------------------------------ apple.com global nav (F1-F4)
// Menus open 80 ms after the pointer arrives (or at once on click, tap or keyboard), slide 8 px in the
// direction of travel between menus, and dim the page behind with the blurred curtain.
const gnav = { open: null, timer: null };
const MENU_ORDER = ["today", "followup", "clinic"];
const flyout = $("gn-flyout");

function openNav(name, { focus = false } = {}) {
  clearTimeout(gnav.timer);
  if (gnav.open === name) return;
  const previous = gnav.open;
  gnav.open = name;
  const panels = [...flyout.querySelectorAll(".gn-panel")];
  panels.forEach((panel) => panel.classList.remove("active", "compact-all", "entering", "entering-forward", "entering-back"));
  const shown = name === "all" ? panels : panels.filter((panel) => panel.dataset.panel === name);
  const motion = !previous || previous === "all" || name === "all" ? "entering"
    : MENU_ORDER.indexOf(name) > MENU_ORDER.indexOf(previous) ? "entering-forward" : "entering-back";
  shown.forEach((panel) => {
    panel.classList.add(name === "all" ? "compact-all" : "active");
    void panel.offsetWidth;             // restart the slide when switching menus
    panel.classList.add(motion);
  });
  if (name === "all") flyout.querySelectorAll(".gn-col > *").forEach((row, i) => row.style.setProperty("--row", i));
  flyout.style.height = name === "all" ? "" : `${shown[0].offsetHeight}px`;
  flyout.classList.add("open");
  $("gn-curtain").classList.add("open");
  $("globalnav").classList.add("open");
  document.querySelectorAll("[data-menu]").forEach((button) => button.setAttribute("aria-expanded", String(button.dataset.menu === name)));
  if (focus) shown[0].querySelector("a, button")?.focus();
}

function closeNav() {
  clearTimeout(gnav.timer);
  if (!gnav.open) return;
  gnav.open = null;
  flyout.style.height = "0px";
  flyout.classList.remove("open");
  $("gn-curtain").classList.remove("open");
  $("globalnav").classList.remove("open");
  document.querySelectorAll("[data-menu]").forEach((button) => button.setAttribute("aria-expanded", "false"));
}

document.querySelectorAll("[data-menu]").forEach((button) => {
  button.addEventListener("pointerenter", (e) => {
    if (e.pointerType !== "mouse" || button.dataset.menu === "all") return;
    clearTimeout(gnav.timer);
    gnav.timer = setTimeout(() => openNav(button.dataset.menu), 80);
  });
  button.addEventListener("click", (e) => {
    if (gnav.open === button.dataset.menu) closeNav();
    else openNav(button.dataset.menu, { focus: e.detail === 0 });   // keyboard: move focus into the menu
  });
});
for (const area of [$("globalnav"), flyout]) {
  area.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse" && gnav.open) clearTimeout(gnav.timer); });
  area.addEventListener("pointerleave", (e) => {
    if (e.pointerType !== "mouse" || gnav.open === "all") return;
    clearTimeout(gnav.timer);
    gnav.timer = setTimeout(closeNav, 160);
  });
}
$("gn-curtain").addEventListener("click", closeNav);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !gnav.open) return;
  const menu = gnav.open;
  closeNav();
  document.querySelector(`[data-menu="${menu}"]`)?.focus();
});
matchMedia("(max-width: 833px)").addEventListener("change", closeNav);

// The section bar turns frosted once the page scrolls under it (F7).
const markScrolled = () => document.body.classList.toggle("scrolled", window.scrollY > 4);
window.addEventListener("scroll", markScrolled, { passive: true });
markScrolled();

window.addEventListener("hashchange", () => render(sectionName(location.hash.slice(1)) || "live"));

// "/" jumps to search, as in Apple's apps; nothing else overrides the browser's own shortcuts.
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, select, textarea, [contenteditable]")) return;
  e.preventDefault();
  if (currentTab !== "appointments") showTab("appointments");
  $("appt-search").focus();
});

// ------------------------------------------------------------------ live call
// Up to MAX_CONCURRENT_CALLS calls can be on at once (11 Oct): each has its own state, and the
// Live tab follows one of them (`live`); the chips above the transcript switch between them.
function newLiveState(callId = null) {
  return {
    callId, started: callId ? new Date() : null, ended: false, state: callId ? "listening" : "idle", lines: [],
    interim: "", turns: new Map(), goal: null, tier: null, entities: {}, outcome: null, operator: false,
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
    const same = turn !== undefined && turn !== null
      ? st.lines.find((line) => line.who === "emma" && line.turn === turn) : null;
    if (same) {
      same.text = text;
      return;
    }
    const last = st.lines[st.lines.length - 1];
    if (last) last.closed = true;
    st.lines.push({ who: "emma", text, closed: true, turn });
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
      if (!st.ended) st.state = event.state;
      break;
    case "turn":
      if (event.phase === "start" && !st.ended) st.state = "speaking";
      break;
    case "metrics":
      st.turns.set(event.turn, event);
      if (event.tier !== undefined && event.tier !== null) st.tier = event.tier;
      if (event.goal_after || event.step_after) st.goal = event.goal_after || `step ${event.step_after}`;
      break;
    case "call_turn": {
      const meta = event.meta || {};
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
      if (currentTab === "health" || currentTab === "privacy") reloadSystemSoon();
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
      if (currentTab === "rebooking") reloadRecoverySoon();
      if (event.type === "ring") toast(`Emma is calling ${event.to_name || "a patient"}…`);
      break;
    default:
      return;
  }
  if (currentTab === "live") renderLive();
}

function renderCallSwitcher() {
  const box = $("live-calls");
  const active = [...liveCalls.values()].filter((st) => !st.ended || st === live);
  box.hidden = active.length < 2;
  box.replaceChildren(...active.map((st, i) => el("button", {
    type: "button", class: `chip${st === live ? " active" : ""}`, title: st.callId,
    text: `Call ${i + 1}${st.ended ? " (ended)" : ""}`, onclick: () => followCall(st.callId),
  })));
}

function renderLive() {
  renderCallSwitcher();
  const hasCall = Boolean(live.callId);
  $("live-empty").hidden = hasCall;
  const pill = $("live-state");
  const label = { idle: "No call", listening: "Listening", thinking: "Thinking", speaking: "Emma is speaking", ended: "Call ended" };
  pill.className = `state-pill ${live.state}`;
  pill.lastElementChild.textContent = label[live.state] || live.state;
  pill.firstElementChild.classList.toggle("live", hasCall && !live.ended);
  $("live-call").textContent = live.callId || "–";
  $("live-goal").textContent = live.goal || "–";
  $("live-tier").textContent = live.tier === null ? "–" : TIER_LABEL[live.tier] ?? String(live.tier);
  $("live-outcome").textContent = live.outcome || (hasCall && !live.ended ? "in progress" : "–");
  renderDuration();
  const active = hasCall && !live.ended;
  $("live-controls").hidden = !active;
  $("op-takeover").hidden = live.operator;
  $("op-handback").hidden = !live.operator;
  $("op-form").hidden = !live.operator;

  const box = $("live-transcript");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  // Only lines that weren't on screen before slide in; a line that grows sentence by sentence stays put.
  const shown = box.dataset.call === String(live.callId) ? Number(box.dataset.lines || 0) : live.lines.length;
  box.dataset.call = String(live.callId);
  box.dataset.lines = String(live.lines.length);
  const nodes = live.lines.map((line, i) => el("div", { class: `line ${line.who}${i >= shown ? " new" : ""}` },
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
  $("live-duration").textContent = live.started
    ? (live.ended ? "Ended" : `${fmtTime(live.started.toISOString())} · ${length(live.started.toISOString())}`) : "–";
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
    lastSeq = 0;                // the server replays the calls in progress from the start
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
  const list = $("appt-rows");
  renderFilterTokens();
  try {
    appt.rows = (await api(`/dashboard/api/appointments?${apptQuery()}`)).appointments;
  } catch (err) {
    list.replaceChildren(emptyItem(err.message));
    return;
  }
  renderAppointments();
}
const reloadAppointmentsSoon = debounce(loadAppointments, 400);

// Active filters show as tokens under the toolbar, each removable on its own.
const FILTERS = [["appt-branch", "Branch"], ["appt-doctor", "Doctor"], ["appt-status", "Status"], ["appt-group", "Grouped"]];
function renderFilterTokens() {
  const active = FILTERS.filter(([id]) => $(id).value);
  $("appt-tokens").replaceChildren(...active.map(([id, label]) => {
    const select = $(id);
    const text = select.options[select.selectedIndex].text;
    return el("span", { class: "token" }, `${label}: ${text}`,
      el("button", { type: "button", "aria-label": `Remove filter ${label}: ${text}`, text: "×", onclick: () => {
        select.value = "";
        if (id === "appt-branch") fillDoctorFilter();
        if (id === "appt-group") { renderFilterTokens(); renderAppointments(); } else loadAppointments();
      } }));
  }));
  $("appt-filter-button").lastChild.textContent = active.length ? `Filter (${active.length})` : "Filter";
}

function appointmentActions(r, now) {
  const changeable = (r.status === "booked" || r.status === "needs_reschedule") && new Date(r.start) > now;
  if (changeable) {
    return [{ label: "Move…", run: () => openBook("move", r) }, { label: "Cancel appointment…", danger: true, run: () => openCancel(r) }];
  }
  if (r.status === "booked" && new Date(r.start) <= now) {
    return [{ label: "Attended", run: () => markAttendance(r, "completed") }, { label: "No-show", run: () => markAttendance(r, "no_show") }];
  }
  return [];
}

function renderAppointments() {
  const list = $("appt-rows");
  const group = $("appt-group").value;
  const rows = [...appt.rows];
  if (group) rows.sort((a, b) => a[group].localeCompare(b[group]) || a.start.localeCompare(b.start));
  const active = rows.filter((r) => r.status === "booked" || r.status === "needs_reschedule").length;
  const where = appt.scope === "day" ? prettyDay(appt.day) : { upcoming: "Upcoming", past: "Past", all: "All" }[appt.scope];
  $("appt-summary").textContent = `${where} · ${rows.length} shown, ${active} active`;
  if (!rows.length) {
    const sunday = appt.scope === "day" && new Date(`${appt.day}T12:00:00`).getDay() === 0;
    list.replaceChildren(emptyItem(sunday ? "The clinic is closed on Sundays." : "No appointments match."));
    return;
  }
  const out = [];
  let lastGroup = null;
  const now = new Date();
  for (const r of rows) {
    if (group && r[group] !== lastGroup) {
      lastGroup = r[group];
      out.push(el("li", { class: "group-head", text: lastGroup }));
    }
    const day = appt.scope === "day";
    const actions = appointmentActions(r, now);
    const more = actions.length ? el("button", {
      class: "btn small icon-only", type: "button", "aria-haspopup": "menu",
      "aria-label": `Actions for ${r.patient_name}, ${r.time}`,
    }) : el("span", { class: "btn small icon-only", "aria-hidden": "true", style: null });
    if (actions.length) {
      more.append(svgIcon("i-more"));
      more.addEventListener("click", () => openMenu(more, actions));
    } else {
      more.style.visibility = "hidden";
    }
    const synced = r.calendar_synced;
    const item = el("li", {}, el("div", { class: `row appt-row${r.status === "cancelled" ? " muted" : ""}` },
      el("div", { class: "row-time" }, day ? r.time : fmtDateTime(r.start).replace(/, (\d\d:\d\d)$/, ""),
        el("span", { class: "muted", text: day ? `to ${r.end_time}` : fmtTime(r.start) })),
      el("div", {},
        el("div", { class: "row-title" }, r.patient_name,
          r.patient_age ? el("span", { class: "muted", text: ` · ${r.patient_age} y` }) : null),
        el("div", { class: "row-sub", text: `${r.service} · ${r.doctor}` }),
        r.name_unverified ? el("span", { class: "badge warn", text: "Name unverified" }) : null),
      el("div", { class: "row-where row-sub" }, el("div", { class: "num", text: r.phone_display }), el("div", { text: r.branch })),
      el("div", { class: "row-status" },
        el("span", { class: `cal-mark${synced ? "" : " waiting"}`, text: synced ? "Calendar ✓" : "Calendar …",
          title: synced ? "In Google Calendar" : "Waiting to sync" }),
        el("span", { class: `badge ${r.status}`, text: STATUS_LABEL[r.status] || r.status, title: r.cancel_reason || null })),
      more));
    out.push(item);
  }
  list.replaceChildren(...out);
}

function svgIcon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
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
$("appt-group").addEventListener("change", () => { renderFilterTokens(); renderAppointments(); });
$("appt-search").addEventListener("input", debounce(loadAppointments, 300));
$("appt-export").addEventListener("click", () => {
  // A plain navigation: the browser downloads the file with the session cookie.
  location.href = `/dashboard/api/appointments.csv?${apptQuery()}`;
});
$("appt-new").addEventListener("click", () => openBook("book"));

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
  present($("dlg-cancel"));
}

$("cancel-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("cancel-confirm");
  button.disabled = true;
  try {
    await api(`/dashboard/api/appointments/${encodeURIComponent(cancelTarget.id)}/cancel`, {
      method: "POST", body: { reason: $("cancel-reason").value, version: cancelTarget.version, idem_key: newKey() },
    });
    dismiss($("dlg-cancel"));
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
  present($("dlg-book"));
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
    dismiss($("dlg-book"));
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
  const listBox = $("task-rows");
  let list;
  try {
    list = (await api(`/dashboard/api/tasks?status=${taskStatus}`)).tasks;
  } catch (err) {
    listBox.replaceChildren(emptyItem(err.message));
    return;
  }
  if (!list.length) {
    listBox.replaceChildren(emptyItem(taskStatus === "open" ? "Nothing waiting. Nice." : "No tasks."));
    return;
  }
  const PRIORITY = { urgent: ["!!!", "Urgent"], high: ["!!", "High"] };
  listBox.replaceChildren(...list.map((t) => {
    const done = t.status === "done";
    const title = t.note || KIND_LABEL[t.kind] || t.kind;
    const check = el("button", {
      class: "task-check", type: "button", role: "checkbox", "aria-checked": String(done), "aria-label": `Done: ${title}`,
    });
    const item = el("li", {});
    check.addEventListener("click", () => setTaskDone(t, !done, check, item));
    const [marks, word] = PRIORITY[t.priority] || [];
    item.append(el("div", { class: `row task-row${done ? " muted" : ""}` },
      check,
      el("div", {},
        el("div", { class: "row-title", text: title }),
        el("div", { class: "row-sub task-meta" },
          t.note ? el("span", { text: KIND_LABEL[t.kind] || t.kind }) : null,
          t.phone_display ? el("span", { class: "num", text: t.phone_display }) : null,
          el("span", { text: `Added ${fmtDateTime(t.created_at)}` }),
          t.due_at ? el("span", { text: `Due ${fmtDateTime(t.due_at)}` }) : null,
          t.call_id ? el("button", {
            class: "link-btn", type: "button", text: "Read the call",
            onclick: () => { showTab("calls"); openCall(t.call_id); },
          }) : null)),
      marks ? el("span", { class: `priority ${t.priority}` }, el("span", { "aria-hidden": "true", text: marks }), word)
        : el("span")));
    return item;
  }));
}

// Checking a task off: the circle fills at once, the row fades out, and Undo puts it back.
async function setTaskDone(t, done, check, item) {
  check.setAttribute("aria-checked", String(done));
  check.disabled = true;
  try {
    await api(`/dashboard/api/tasks/${t.id}`, { method: "POST", body: { done } });
  } catch (err) {
    toast(err.message, "error");
    check.setAttribute("aria-checked", String(!done));
    check.disabled = false;
    return;
  }
  refreshSoon();
  const leaves = (done && taskStatus === "open") || (!done && taskStatus === "done");
  if (leaves) item.firstElementChild.classList.add("leaving");
  setTimeout(loadTasks, leaves && !REDUCED_MOTION.matches ? 800 : 0);
  toast(done ? "Marked done." : "Marked not done.", "", {
    label: "Undo",
    run: async () => {
      try {
        await api(`/dashboard/api/tasks/${t.id}`, { method: "POST", body: { done: !done } });
      } catch (err) { toast(err.message, "error"); }
      loadTasks();
      refreshSoon();
    },
  });
}

document.querySelectorAll("#task-filter button").forEach((button) => button.addEventListener("click", () => {
  taskStatus = button.dataset.status;
  loadTasks();
}));

// ------------------------------------------------------------------ calls
let selectedCall = null;

async function loadCalls() {
  const listBox = $("call-rows");
  let list;
  try {
    list = (await api("/dashboard/api/calls?limit=100")).calls;
  } catch (err) {
    listBox.replaceChildren(emptyItem(err.message));
    return;
  }
  if (!list.length) {
    listBox.replaceChildren(emptyItem("No calls yet."));
    return;
  }
  const OUTCOME_BADGE = { booked: "booked", rescheduled: "rescheduled", cancelled: "cancelled", completed: "completed",
    transferred: "transferred" };
  listBox.replaceChildren(...list.map((c) => {
    const kind = (c.outcome || "").split("@")[0];
    const turns = c.turns === c.kept_turns ? `${c.turns} turns` : `${c.kept_turns} of ${c.turns} turns kept`;
    const row = el("button", {
      type: "button", class: "row call-row", "data-call": c.id,
      "aria-current": c.id === selectedCall ? "true" : null,
    },
      el("div", {},
        el("div", { class: "row-title", text: fmtDateTime(c.started_at) }),
        el("div", { class: "row-sub num", text: [c.phone_display, CHANNEL_LABEL[c.channel], turns].filter(Boolean).join(" · ") })),
      el("div", { class: "row-status" },
        el("span", { class: "row-sub num", text: c.ended_at ? length(c.started_at, c.ended_at) : "Live" }),
        el("span", { class: `badge ${OUTCOME_BADGE[kind] || ""}`.trim(), title: c.outcome || "", text: callOutcome(c.outcome) })));
    row.addEventListener("click", () => openCall(c.id));
    return el("li", {}, row);
  }));
  if (selectedCall) openCall(selectedCall, { quiet: true });
}

$("calls-back").addEventListener("click", () => {
  $("view-calls").classList.remove("detail");
  document.querySelector(`#call-rows [data-call="${CSS.escape(selectedCall || "")}"]`)?.focus();
});

async function openCall(callId, { quiet = false } = {}) {
  selectedCall = callId;
  if (!quiet) $("view-calls").classList.add("detail");
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
    const meta = [t.tier !== null ? (TIER_LABEL[t.tier] || `tier ${t.tier}`) : null, perceived ? `${ms(perceived)} ms` : null,
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
    el("div", { class: "card-head" }, el("h2", { text: fmtDateTime(call.started_at) }), deleteButton),
    el("dl", { class: "kv" },
      el("dt", { text: "Call ID" }), el("dd", { class: "mono", text: call.id }),
      el("dt", { text: "Length" }), el("dd", { text: call.ended_at ? length(call.started_at, call.ended_at) : "still live" }),
      el("dt", { text: "Direction" }), el("dd", { text: call.direction }),
      el("dt", { text: "Outcome" }), el("dd", { text: callOutcome(call.outcome), title: call.outcome || "" }),
      el("dt", { text: "Came in by" }), el("dd", { text: CHANNEL_LABEL[call.channel] || "–" }),
      el("dt", { text: "Caller" }), el("dd", { class: "num", text: call.phone_display || "–" }),
      el("dt", { text: "Transcript" }), el("dd", { text: kept ? `kept until ${fmtDateTime(call.purge_after)}` : "not kept (caller's request or deleted)" }),
      call.appointments.length ? [el("dt", { text: "Appointments" }), el("dd", {},
        ...call.appointments.map((a) => el("div", { text: `${a.service}, ${fmtDateTime(a.start_utc)}, ${a.branch} (${STATUS_LABEL[a.status] || a.status})` })))] : null,
      call.tasks.length ? [el("dt", { text: "Tasks" }), el("dd", {},
        ...call.tasks.map((t) => el("div", { text: `${KIND_LABEL[t.kind] || t.kind} · ${t.priority} · ${t.status}` })))] : null),
    el("h3", { class: "card-head", text: "Transcript" }),
    el("div", { class: "transcript" }, ...(turns.length ? turns : [el("div", { class: "empty", text: "No turns recorded." })])));
  document.querySelectorAll("#call-rows [data-call]").forEach((row) => {
    const on = row.dataset.call === callId;
    if (on) row.setAttribute("aria-current", "true"); else row.removeAttribute("aria-current");
  });
}

// ------------------------------------------------------------------ system
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

async function loadStaff() {
  let me;
  try { me = await api("/dashboard/api/auth"); } catch { return; }
  $("staff-card").hidden = me.role !== "admin";
  if (me.role !== "admin") return;
  $("staff-me").textContent = `You're signed in as ${me.user}.`;
  let data;
  try { data = await api("/dashboard/api/staff"); } catch (err) { $("staff-result").textContent = err.message; return; }
  $("staff-rows").replaceChildren(...(data.staff.length ? data.staff.map((s) => el("tr", { class: s.active ? "" : "muted-row" },
    el("td", { class: "mono", text: s.username }),
    el("td", { text: s.display_name }),
    el("td", { text: s.role }),
    el("td", { class: "nowrap", text: s.last_login_at ? fmtDateTime(s.last_login_at) : "never" }),
    el("td", { class: "actions" }, el("button", { class: "btn small", type: "button",
      text: s.active ? "Switch off" : "Switch on",
      onclick: async () => {
        try {
          await api(`/dashboard/api/staff/${encodeURIComponent(s.username)}/active`, { method: "POST", body: { active: !s.active } });
          loadStaff();
        } catch (err) { toast(err.message, "error"); }
      } })))) : [emptyRow(5, "Only the clinic password so far. Add a login for each person.")]));
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
  let data, me;
  try {
    [data, me] = await Promise.all([api("/dashboard/api/doctors"), api("/dashboard/api/auth")]);
  } catch (err) { $("doctor-result").textContent = err.message; return; }
  const isAdmin = me.role === "admin";
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
      el("td", { class: "small", text: d.services.join(", ") }),
      el("td", { class: "small", text: hours(d.rota) }),
      el("td", { class: "actions" }, ...(isAdmin ? [
        el("button", { class: "btn small", type: "button", text: "Edit", onclick: () => editDoctor(d, row, data) }),
        el("button", { class: "btn small ghost", type: "button", text: d.active ? "Switch off" : "Switch on",
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
  const note = el("p", { class: "muted small", text: "Shifts like 09:00-13:00, 16:00-20:00, or off." });
  const editor = el("tr", { class: "editor-row" }, el("td", { colspan: "5" },
    el("div", { class: "rota-grid" }, ...data.days.map((day) => el("label", {}, el("span", { text: day }), inputs[day]))),
    el("div", { class: "checks" }, ...services),
    note,
    el("div", { class: "actions" },
      el("button", { class: "btn small", type: "button", text: "Save", onclick: async () => {
        try {
          const rota = Object.fromEntries(data.days.map((day) => [day, inputs[day].value]));
          const chosen = services.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
          const out = await api(`/dashboard/api/doctors/${d.id}/rota`, { method: "POST", body: { rota } });
          if (chosen.join("|") !== d.services.join("|")) {
            await api(`/dashboard/api/doctors/${d.id}/services`, { method: "POST", body: { services: chosen } });
          }
          $("doctor-result").textContent = out.affected.length
            ? `Saved. ${out.affected.length} upcoming booking(s) with ${d.spoken_name} are now outside these hours: block the doctor for those days in Rebooking to have Emma move them.`
            : `Saved. Emma offers ${d.spoken_name}'s new hours from the next call.`;
          loadDoctors();
        } catch (err) { note.textContent = err.message; }
      } }),
      el("button", { class: "btn small ghost", type: "button", text: "Cancel", onclick: () => editor.remove() }))));
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
  $("closure-rows").replaceChildren(...(data.closures.length ? data.closures.map((c) => el("tr", {},
    el("td", { class: "nowrap", text: prettyDay(c.date) }),
    el("td", { text: c.branch || "All branches" }),
    el("td", { text: c.reason }),
    el("td", { class: "actions" }, el("button", { class: "btn small", type: "button", text: "Remove",
      onclick: async () => {
        try { await api(`/dashboard/api/closures/${c.id}/delete`, { method: "POST", body: {} }); loadClosures(); }
        catch (err) { toast(err.message, "error"); }
      } })))) : [emptyRow(4, "No closures coming up.")]));
}
$("closure-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const out = await api("/dashboard/api/closures", { method: "POST", body: {
      date: $("closure-date").value, branch: $("closure-branch").value, reason: $("closure-reason").value } });
    $("closure-result").textContent = out.affected.length
      ? `Added. ${out.affected.length} appointment(s) already booked that day: block the doctors in Rebooking to have Emma call them.`
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
    box.replaceChildren(el("p", { class: "muted", text: "No calls in the last 7 days." }));
    $("perf-summary").replaceChildren(el("dt", { text: "Calls" }), el("dd", { text: "None in the last 7 days" }));
    return;
  }
  const sec = (v) => (v === null || v === undefined ? "–" : `${(v / 1000).toFixed(1)} s`);
  const pct = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : "–");
  // The three things the owner asks: how many calls, how quickly Emma answers, what a call costs.
  const per = (f) => channels.map(([name, c]) => (channels.length > 1 ? `${CHANNEL_LABEL[name] || name} ${f(c)}` : f(c))).join(" · ");
  $("perf-summary").replaceChildren(
    el("dt", { text: "Calls" }), el("dd", { class: "num", text: per((c) => String(c.calls)) }),
    el("dt", { text: "Typical reply" }), el("dd", { class: "num", text: per((c) => sec(c.model_p50_ms ?? c.simple_p50_ms)) }),
    el("dt", { text: "Slow replies (fallback)" }), el("dd", { class: "num", text: per((c) => pct(c.fallback, c.model + c.fallback)) }),
    el("dt", { text: "Cost per call" }), el("dd", { class: "num",
      text: per((c) => (c.metered_calls ? `$${c.cost_per_call_usd.toFixed(3)}` : "–")) }));
  const row = (label, f) => el("tr", {}, el("th", { text: label }), ...channels.map(([, c]) => el("td", { class: "num", text: f(c) })));
  box.replaceChildren(el("table", { class: "kv-table" },
    el("thead", {}, el("tr", {}, el("th", {}), ...channels.map(([name, c]) =>
      el("th", { text: `${CHANNEL_LABEL[name] || name} (${c.calls} calls)` })))),
    el("tbody", {},
      row("Replies", (c) => String(c.replies)),
      row("Answered by Emma's rules", (c) => `${c.rules} (${pct(c.rules, c.replies)})`),
      row("Answered by the language model", (c) => `${c.model} (${pct(c.model, c.replies)})`),
      row("Fallback, model too slow or down", (c) => `${c.fallback} (${pct(c.fallback, c.model + c.fallback)} of model turns)`),
      row("Simple reply, typical / slowest 5%", (c) => `${sec(c.simple_p50_ms)} / ${sec(c.simple_p95_ms)}`),
      row("Model reply, typical / slowest 5%", (c) => `${sec(c.model_p50_ms)} / ${sec(c.model_p95_ms)}`),
      row("Of that, deciding the caller finished", (c) => sec(c.end_of_speech_p50_ms)),
      row("Estimated cost per call (7-day total)", (c) => (c.metered_calls
        ? `$${c.cost_per_call_usd.toFixed(3)} ($${c.cost_total_usd.toFixed(2)}, ${c.metered_calls} calls)`
        : "–")))));
}

// The old System tab is three sections now: Health (is everything working), Setup (doctors, closures,
// sign-ins) and Privacy (data requests, do-not-call, audit). Health and Privacy share /api/system.
function loadSetup() {
  loadDoctors();
  loadClosures();
  loadStaff();
}

async function loadPrivacy() {
  let data;
  try { data = await api("/dashboard/api/system"); } catch (err) { toast(err.message, "error"); return; }
  renderDoNotCall(data);
  renderAudit(data);
}

async function loadHealthView() {
  loadPerformance();
  let data;
  try {
    data = await api("/dashboard/api/system");
  } catch (err) {
    $("sync-detail").textContent = err.message;
    return;
  }
  renderSync(data);
  renderMessages(data);
  loadHealth(data);
}

function loadSystem() {
  return currentTab === "privacy" ? loadPrivacy() : loadHealthView();
}

function renderSync(data) {
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
}

function renderDoNotCall(data) {
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
}

function renderMessages(data) {
  const ms = data.message_status || {};
  const mc = data.message_counts || {};
  $("msg-state").className = `badge ${ms.sender === "twilio" ? "ok" : "warn"}`;
  $("msg-state").textContent = ms.sender === "twilio" ? `Sending (${ms.channel})` : ms.sender === "off" ? "Off" : "Log only";
  $("msg-detail").textContent = [ms.reason ? `${ms.reason}.` : null,
    `${mc.pending || 0} waiting, ${mc.sent || 0} sent, ${mc.logged || 0} logged, ${mc.failed || 0} failed.`]
    .filter(Boolean).join(" ");
  const MSG_KIND = { booked: "Confirmation", moved: "Moved", cancelled: "Cancelled", reminder: "Reminder" };
  $("msg-rows").replaceChildren(...((data.messages || []).length ? data.messages.map((m) => el("tr", {},
    el("td", { text: MSG_KIND[m.kind] || m.kind }),
    el("td", { class: "num nowrap", text: m.phone_display }),
    el("td", { class: "small", text: m.body }),
    el("td", {}, el("span", { class: `badge ${m.status}`, title: m.last_error || "", text: m.status })),
    el("td", { class: "nowrap", text: fmtDateTime(m.sent_at || m.due_at) }))) : [emptyRow(5, "No messages yet.")]));
}

function renderAudit(data) {
  $("audit-rows").replaceChildren(...data.audit.map((a) => el("tr", {},
    el("td", { class: "nowrap", text: fmtDateTime(a.ts) }),
    el("td", { text: a.actor }),
    el("td", { text: a.action.replace(/_/g, " ") }),
    el("td", { class: "small", title: `${a.entity} ${a.entity_id}`, text: a.what || `${a.entity} ${a.entity_id}` }))));
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
  add("Dashboard sign-in", yes(system.auth.configured, system.auth.persistent_sessions ? "On" : "On (logins end on restart)", "Locked"));
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
  button.textContent = ticked ? `Start rebooking calls (${ticked})` : "Start rebooking calls";
}

$("campaign-start").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#preview-rows input:checked")].map((b) => b.value);
  if (!ids.length || !recovery.blockId) return;
  const text = `Emma will call about ${ids.length} appointment(s), one patient at a time, and offer only valid new times.`;
  if (!await confirmAction("Start rebooking calls?", text, "Start calls")) return;
  try {
    await api("/dashboard/api/recovery/campaigns", { method: "POST", body: { block_id: recovery.blockId, appointment_ids: ids } });
    toast("Rebooking calls started.");
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
    box.replaceChildren(el("div", { class: "empty", text: "No rebooking calls yet." }));
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

document.querySelectorAll("[data-signout]").forEach((button) => button.addEventListener("click", async () => {
  try { await fetch("/dashboard/api/logout", { method: "POST", credentials: "same-origin" }); } finally {
    location.replace("/dashboard/login");
  }
}));

async function start() {
  await refreshOverview();
  try { await loadCatalog(); } catch (err) { toast(err.message, "error"); }
  api("/dashboard/api/auth").then((me) => {
    if (me && me.user) {
      $("account-who").textContent = me.user;
      $("account-label").textContent = `Signed in as ${me.user}`;
    }
  }).catch(() => {});
  connectEvents();
  const wanted = sectionName(location.hash.slice(1)) || sectionName(recall("tab")) || "live";
  if (location.hash !== `#${wanted}`) history.replaceState(null, "", `#${wanted}`);
  currentTab = null;
  render(wanted);
  setInterval(refreshOverview, 30000);
}

start();
