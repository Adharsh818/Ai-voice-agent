// Talk page chrome. app.js runs the call and dispatches "emma-call-state"; this only mirrors
// that state onto the page:
// - the hero cross-fades to a status line, and the call button becomes "End call";
// - the conversation shows the caller's last line above Emma's, from app.js's live #caption;
// - after the caller sends a line, Emma shows she is working on it until her reply arrives;
// - a call that ends leaves a short "Call ended" moment instead of snapping back;
// - the level meter follows the voice, and the pill bar glides when its pills change.

import { capsuleNav, bubbles, materialise, flip, levelMeter } from "/static/ui/emma-ui.js";

const body = document.body;
const orb = document.getElementById("orb");
const callButton = document.getElementById("call-button");
const typeInstead = document.getElementById("type-instead");
const action = document.getElementById("action");
const statusText = document.getElementById("status-text");
const caption = document.getElementById("caption");
const transcript = document.getElementById("transcript");
const callerLine = document.getElementById("line-caller");
const emmaLine = document.getElementById("line-emma");
const typedForm = document.getElementById("typed");
const typedText = document.getElementById("typed-text");
const bar = document.getElementById("call-bar");
const dock = document.getElementById("dock");

const IN_CALL = ["connecting", "listening", "thinking", "speaking"];
const STATUS = {
  connecting: "Calling Emma…",
  listening: "Emma is listening",
  thinking: "One moment…",
  speaking: "Emma is speaking",
};
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// The call button already does what the orb does, so the orb is for pointers only: one
// "End call" for screen readers, not two. app.js names the orb on every state change, so the
// name is taken off again each time.
function quietOrb() {
  orb.setAttribute("aria-hidden", "true");
  orb.setAttribute("role", "presentation");
  if (orb.hasAttribute("aria-label")) orb.removeAttribute("aria-label");
  if (orb.hasAttribute("aria-pressed")) orb.removeAttribute("aria-pressed");
}
orb.tabIndex = -1;
quietOrb();
new MutationObserver(quietOrb).observe(orb, { attributes: true, attributeFilter: ["aria-label", "aria-pressed"] });

let state = "idle";
let pending = false;
let pendingSince = 0;
let pendingTimer, pendingShowTimer;
let endedTimer;

// The status headline: each wording is held for at least 700ms (a newer one waits, and one that
// is superseded before its turn never shows), and the words cross-fade: out 120ms, in 200ms.
const HOLD_MS = 700;
let shownStatus = "";
let wantedStatus = "";
let lastSwap = -HOLD_MS;
let statusTimer, swapTimer;
function setStatus(text) {
  wantedStatus = text;
  clearTimeout(statusTimer);
  if (text === shownStatus) { clearTimeout(swapTimer); statusText.classList.remove("is-out"); return; }
  // Out of sight (the hero is showing): change at once, so a call opens on the right words.
  if (body.dataset.view !== "status" || !shownStatus) { put(text); return; }
  statusTimer = setTimeout(swap, Math.max(0, lastSwap + HOLD_MS - performance.now()));
}
function put(text) {
  clearTimeout(swapTimer);
  statusText.textContent = text;
  statusText.classList.remove("is-out");
  shownStatus = text;
  lastSwap = performance.now();
}
function swap() {
  const text = wantedStatus;
  if (text === shownStatus) return;
  if (reduced()) { put(text); return; }
  statusText.classList.add("is-out");
  clearTimeout(swapTimer);
  swapTimer = setTimeout(() => put(wantedStatus), 120);
}

function updateStatus() {
  const inCall = IN_CALL.includes(state);
  const view = inCall || body.classList.contains("call-ended") ? "status" : "hero";
  // "One moment…" only once Emma has been working for 700ms: a quick reply never flashes it.
  const working = pending && inCall && state !== "speaking" && performance.now() - pendingSince >= HOLD_MS;
  let text = "";
  if (body.classList.contains("call-ended")) text = "Call ended";
  else if (working) text = STATUS.thinking;
  else if (state === "thinking") text = STATUS.listening;
  else text = STATUS[state] || "";
  if (view === "hero") body.dataset.view = view;
  if (text) setStatus(text);
  body.dataset.view = view;
}

// "Emma is working on it": from the caller's line until her reply (or 15 s, or the call ends).
// The orb and the meter show it after 320ms; the words only after 700ms.
function setPending(on) {
  if (on && pending) return;
  pending = on;
  body.classList.toggle("is-pending", on);
  clearTimeout(pendingTimer);
  clearTimeout(pendingShowTimer);
  if (on) {
    pendingSince = performance.now();
    pendingTimer = setTimeout(() => setPending(false), 15000);
    pendingShowTimer = setTimeout(updateStatus, HOLD_MS + 10);
  }
  updateStatus();
}

// The call button's words cross-fade in place, so its label is never squeezed while the
// bubble changes size and colour underneath it.
let labelTimer;
function setCallLabel(text) {
  clearTimeout(labelTimer);
  if (callButton.textContent === text && !callButton.classList.contains("label-out")) return;
  if (reduced() || !bar.classList.contains("is-settled")) {
    callButton.textContent = text;
    callButton.classList.remove("label-out");
    return;
  }
  callButton.classList.add("label-out");
  labelTimer = setTimeout(() => {
    callButton.textContent = text;
    callButton.classList.remove("label-out");
  }, 110);
}

function clearTranscript() {
  callerLine.textContent = "";
  emmaLine.textContent = "";
  emmaLine.className = "line-emma";
  transcript.classList.remove("has-text");
}

function apply(next, was) {
  state = next;
  body.dataset.call = next;
  const inCall = IN_CALL.includes(next);
  setCallLabel(inCall ? "End call" : "Call Emma");
  if (next !== "idle") {
    clearTimeout(endedTimer);
    body.classList.remove("call-ended");
  }
  if (next === "connecting") clearTranscript();
  if (next === "thinking") setPending(true);          // holds through a quick thinking/listening flip
  if (next === "speaking" || !inCall) setPending(false);
  // A call that ends normally leaves a short goodbye moment, with what was said still shown.
  if (next === "idle" && IN_CALL.includes(was)) {
    body.classList.add("call-ended");
    clearTimeout(endedTimer);
    endedTimer = setTimeout(() => {
      body.classList.remove("call-ended");
      shownStatus = "";
      updateStatus();
      setTimeout(() => { if (state === "idle" && !body.classList.contains("call-ended")) clearTranscript(); }, 400);
    }, 2400);
  }
  // While idle, typing is always on offer next to the call button; app.js hides it in a call
  // and shows it itself when the microphone is blocked.
  if (next === "idle") typeInstead.hidden = false;
  // A deployment that keeps the talk page for signed-in staff: the prompt is an invitation,
  // not an error. (app.js names the button just after it changes state.)
  queueMicrotask(() => body.classList.toggle("needs-signin", next === "lost" && action.textContent === "Sign in"));
  updateStatus();
}

window.addEventListener("emma-call-state", (event) => apply(event.detail.state, event.detail.was));
callButton.addEventListener("click", () => orb.click());

// ------------------------------------------------------------------ the conversation
function scrollToEnd() {
  transcript.scrollTop = transcript.scrollHeight;
  transcript.classList.toggle("is-overflowing", transcript.scrollHeight > transcript.clientHeight + 1);
}
if ("ResizeObserver" in window) new ResizeObserver(scrollToEnd).observe(transcript);

function showCaller(text) {
  const before = callerLine.textContent;
  if (text === before) return;
  const fresh = !before || !text.startsWith(before.slice(0, 12));   // a new line, not a live partial growing
  callerLine.textContent = text;
  if (fresh && !reduced()) { callerLine.classList.remove("is-new"); void callerLine.offsetWidth; callerLine.classList.add("is-new"); }
  transcript.classList.add("has-text");
  scrollToEnd();
}

// Emma's line cross-fades: the old one goes in 150ms, the new one rises in over 500ms (B13).
let emmaSwap;
function showEmma(text, kind) {
  clearTimeout(emmaSwap);
  const put = () => {
    emmaLine.textContent = text;
    emmaLine.className = `line-emma${kind ? ` ${kind}` : ""}`;
    if (!reduced()) { void emmaLine.offsetWidth; emmaLine.classList.add("is-new"); }
    transcript.classList.add("has-text");
    scrollToEnd();
  };
  if (emmaLine.textContent && !reduced()) {
    emmaLine.classList.remove("is-new");
    emmaLine.classList.add("is-out");
    emmaSwap = setTimeout(put, 150);
  } else {
    put();
  }
}

// app.js writes every caption into #caption (screen readers hear it there); draw it here.
new MutationObserver(() => {
  const text = caption.textContent.trim();
  const kind = caption.className;
  if (!text) return;                       // app.js clears the caption; the lines stay until the next call
  if (kind === "user") { showCaller(text); return; }
  if (kind === "notice" && state === "connecting") return;   // the status line already says it
  if (kind === "error" || kind === "notice") { callerLine.textContent = ""; showEmma(text, kind); return; }
  setPending(false);
  showEmma(text, "");
}).observe(caption, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"] });

// A typed line shows at once as the caller's line, and Emma shows she is on it.
// (Capture phase: app.js empties the box in its own submit handler.)
document.addEventListener("submit", (event) => {
  if (event.target !== typedForm) return;
  const text = typedText.value.trim();
  if (!text || !IN_CALL.includes(state)) return;
  showCaller(text);
  setPending(true);
}, true);

// ------------------------------------------------------------------ B10: the meter reads the orb's --lvl, which app.js sets every frame
const meter = levelMeter(document.getElementById("level-meter"), 5);
function tick() {
  const live = state === "listening" || state === "speaking";
  const working = pending || state === "thinking";
  const level = parseFloat(orb.style.getPropertyValue("--lvl")) || 0;
  meter(working && state !== "speaking" ? 0.25 : level, live || working, working && state !== "speaking");
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

// ------------------------------------------------------------------ B1, B2, B3: capsule, pill bar, bubble
capsuleNav(document.getElementById("talk-capsule"));
materialise(bar);                   // before flip(), so a leaving pill is held before anything measures
bubbles(bar);
flip(dock, { selector: ":scope > *" });   // only the bar as a whole glides; its labels cross-fade

// At the end of a call the typed box fades out before it leaves the dock (then the bar glides).
let typedShown = !typedForm.hidden;
new MutationObserver(() => {
  if (typedForm.hidden && typedShown && !reduced()) {
    typedForm.classList.add("is-leaving");
    clearTimeout(typedForm._leave);
    typedForm._leave = setTimeout(() => typedForm.classList.remove("is-leaving"), 200);
  } else if (!typedForm.hidden) {
    clearTimeout(typedForm._leave);
    typedForm.classList.remove("is-leaving");
  }
  typedShown = !typedForm.hidden;
}).observe(typedForm, { attributes: true, attributeFilter: ["hidden"] });

apply(orb.dataset.state || "idle", null);

// The "Staff" link appears only when this deployment has a staff login set up.
fetch("/dashboard/api/auth", { credentials: "same-origin" })
  .then((res) => (res.ok ? res.json() : null))
  .then((auth) => { if (auth && auth.configured) document.getElementById("staff-link").hidden = false; })
  .catch(() => {});
