// Staff login. The password goes to the server once over a same-origin POST;
// the server answers with an HttpOnly cookie this script never sees.

import { reveal, shake } from "/static/ui/emma-ui.js";

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const button = document.getElementById("login-button");
const password = document.getElementById("password");
const group = document.getElementById("field-group");

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
  group.querySelectorAll(".field-float").forEach((f) => f.classList.toggle("invalid", Boolean(message)));
  if (message) shake(group);
}

// Like Apple's sign-in, the button wakes up once there's something to send.
function updateButton() {
  button.disabled = !password.value;
}
password.addEventListener("input", () => {
  updateButton();
  if (!errorBox.hidden) showError("");
});

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
      document.getElementById("locked").hidden = false;
      document.getElementById("locked-reason").textContent = status.reason || "";
      return;
    }
    form.hidden = false;
    password.focus();
  } catch {
    form.hidden = false;
    showError("Can't reach the server. Is it running?");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!password.value) return;
  showError("");
  button.disabled = true;
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
    showError(body.detail || "Couldn't sign in.");
    form.password.select();
  } catch {
    showError("Can't reach the server. Is it running?");
  } finally {
    updateButton();
  }
});

reveal();
checkStatus();
