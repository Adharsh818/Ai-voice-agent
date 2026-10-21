// Talk page chrome. app.js runs the call and dispatches "emma-call-state"; this only mirrors
// that state onto the page: the hero cross-fades to a status line, the call button becomes
// "End call", "Type instead" stays offered while idle, the level meter follows the voice,
// and each new caption line fades in.

import { reveal, capsuleNav, bubbles, materialise, levelMeter } from "/static/ui/emma-ui.js";

const orb = document.getElementById("orb");
const callButton = document.getElementById("call-button");
const typeInstead = document.getElementById("type-instead");
const action = document.getElementById("action");
const status = document.getElementById("call-status");
const caption = document.getElementById("caption");
const bar = document.getElementById("call-bar");

const IN_CALL = ["connecting", "listening", "thinking", "speaking"];
const STATUS = {
  connecting: "Calling Pearl Dental…",
  listening: "Emma is listening",
  thinking: "One moment…",
  speaking: "Emma is speaking",
};

function apply(state) {
  document.body.dataset.call = state;
  const inCall = IN_CALL.includes(state);
  callButton.textContent = inCall ? "End call" : "Call Emma";
  if (STATUS[state]) status.textContent = STATUS[state];
  // While idle, typing is always on offer next to the call button; app.js hides it in a call
  // and shows it itself when the microphone is blocked.
  if (state === "idle") typeInstead.hidden = false;
  // A deployment that keeps the talk page for signed-in staff: the prompt is an invitation,
  // not an error. (app.js names the button just after it changes state.)
  queueMicrotask(() => document.body.classList.toggle("needs-signin", state === "lost" && action.textContent === "Sign in"));
}

window.addEventListener("emma-call-state", (event) => apply(event.detail.state));
callButton.addEventListener("click", () => orb.click());

apply(orb.dataset.state || "idle");

// B13: every new caption line fades in over 500ms (easeOutCubic).
new MutationObserver(() => {
  caption.classList.remove("is-new");
  if (!caption.textContent) return;
  void caption.offsetWidth;
  caption.classList.add("is-new");
}).observe(caption, { childList: true, characterData: true, subtree: true });

// B10: the stretching-pill meter reads the orb's --lvl, which app.js sets every frame.
const meter = levelMeter(document.getElementById("level-meter"), 5);
function tick() {
  const state = document.body.dataset.call;
  const live = state === "listening" || state === "speaking";
  const level = parseFloat(orb.style.getPropertyValue("--lvl")) || 0;
  meter(level, live || state === "thinking");
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

// B1, B2, B3: the capsule, the materialising pill bar and its sliding bubble.
capsuleNav(document.getElementById("talk-capsule"));
bubbles(bar);
materialise(bar);

// The "Staff" link appears only when this deployment has a staff login set up.
fetch("/dashboard/api/auth", { credentials: "same-origin" })
  .then((res) => (res.ok ? res.json() : null))
  .then((auth) => { if (auth && auth.configured) document.getElementById("staff-link").hidden = false; })
  .catch(() => {});
reveal();
