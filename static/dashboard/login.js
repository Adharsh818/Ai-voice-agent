// Staff login. The password goes to the server once over a same-origin POST;
// the server answers with an HttpOnly cookie this script never sees.

const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const button = document.getElementById("login-button");

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

async function checkStatus() {
  try {
    const res = await fetch("/dashboard/api/auth", { credentials: "same-origin" });
    const status = await res.json();
    if (status.logged_in) {
      location.replace("/dashboard/");
      return;
    }
    if (!status.configured) {
      document.getElementById("locked").hidden = false;
      document.getElementById("locked-reason").textContent = status.reason || "";
      return;
    }
    form.hidden = false;
    document.getElementById("password").focus();
  } catch {
    form.hidden = false;
    showError("Can't reach the server. Is it running?");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  button.disabled = true;
  try {
    const res = await fetch("/dashboard/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: form.password.value }),
    });
    if (res.ok) {
      location.replace("/dashboard/");
      return;
    }
    const body = await res.json().catch(() => ({}));
    showError(body.detail || "Couldn't log in.");
    form.password.select();
  } catch {
    showError("Can't reach the server. Is it running?");
  } finally {
    button.disabled = false;
  }
});

checkStatus();
