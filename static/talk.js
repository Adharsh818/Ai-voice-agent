// Talk page chrome (apple.com hero). app.js runs the call and dispatches "emma-call-state";
// this only mirrors that state onto the page: the hero cross-fades to a status line, the
// call button becomes "End call", and "Type instead" stays offered while idle.

import { reveal } from "/static/ui/emma-ui.js";

const orb = document.getElementById("orb");
const callButton = document.getElementById("call-button");
const typeInstead = document.getElementById("type-instead");
const status = document.getElementById("call-status");

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
}

window.addEventListener("emma-call-state", (event) => apply(event.detail.state));
callButton.addEventListener("click", () => orb.click());

apply(orb.dataset.state || "idle");
reveal();
