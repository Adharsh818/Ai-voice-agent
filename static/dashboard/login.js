// Staff login. The password goes to the server once over a same-origin POST;
// the server answers with an HttpOnly cookie this script never sees.

import { shakeX, capsuleNav } from "/static/ui/emma-ui.js";

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const button = document.getElementById("login-button");
const password = document.getElementById("password");
const group = document.getElementById("field-group");

const username = document.getElementById("username");

// The mark lands, then eases to its small size as the ring and the form fade in (B20). This
// starts on the first frame and never waits for the network, so the page reads within ~500ms.
function ready() {
  void document.body.offsetWidth;          // commit the start state, then transition from it
  document.body.classList.add("is-ready");
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
  // Only the field at fault goes red: the password, and the user name only if one was typed.
  document.getElementById("password-field").classList.toggle("is-error", Boolean(message));
  document.getElementById("username-field").classList.toggle("is-error", Boolean(message) && Boolean(username.value.trim()));
  if (message) shakeX(group);
}

function busy(on) {
  button.classList.toggle("is-busy", on);
  button.setAttribute("aria-busy", String(on));
}

// Like Apple's sign-in, the button wakes up once there's something to send.
function updateButton() {
  button.disabled = !password.value;
}
password.addEventListener("input", () => {
  updateButton();
  if (!errorBox.hidden) showError("");
});
username.addEventListener("input", () => { if (!errorBox.hidden) showError(""); });

// After signing in, go back where the visitor came from (the talk page sends ?next=/),
// but only to a page on this site.
function afterLogin() {
  const next = new URLSearchParams(location.search).get("next") || "";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard/";
}

async function checkStatus() {
  try {
    const res = await fetch("/dashboard/api/auth", { credentials: "same-origin" });
    const status = await res.json();
    if (status.logged_in) {
      location.replace(afterLogin());
      return;
    }
    if (!status.configured) {
      form.hidden = true;
      document.getElementById("locked").hidden = false;
      document.getElementById("locked-reason").textContent = status.reason || "";
    }
  } catch {
    showError("Can't reach the server. Is it running?");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!password.value) return;
  showError("");
  button.disabled = true;
  busy(true);
  try {
    const res = await fetch("/dashboard/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    if (res.ok) {
      location.replace(afterLogin());
      return;
    }
    const body = await res.json().catch(() => ({}));
    busy(false);
    showError(body.detail || "Couldn't sign in.");
    form.password.select();
  } catch {
    busy(false);
    showError("Can't reach the server. Is it running?");
  } finally {
    updateButton();
  }
});

capsuleNav(document.getElementById("signin-capsule"));
// Show the form at once (it stays hidden without JavaScript, so it can never submit as a GET);
// the status check only redirects a signed-in visitor or swaps in the locked notice.
form.hidden = false;
password.focus();
ready();
checkStatus();
