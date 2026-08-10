async function api(path, options = {}) {
  // Mockup mode (Settings → Appearance): serve canned demo data, block writes.
  if (window.PA_MOCK?.enabled) {
    return window.PA_MOCK.respond(path, options);
  }
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || res.statusText);
  }
  return data;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(state, label) {
  const chip = document.getElementById("status-chip");
  const health = document.getElementById("health");
  if (chip) chip.dataset.state = state;
  if (health) health.textContent = label;
}

/* ————— Toasts ————— */

function toast(message, tone = "info", timeout = 4200) {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.tone = tone;
  const icon =
    tone === "error" || tone === "warn"
      ? "i-alert"
      : tone === "ok"
        ? "i-check"
        : "i-file";
  el.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#${icon}" /></svg><span>${escapeHtml(message)}</span>`;
  stack.appendChild(el);
  window.setTimeout(() => {
    el.dataset.leaving = "true";
    window.setTimeout(() => el.remove(), 240);
  }, timeout);
}

/* Two-step destructive confirm: first click arms the button, second confirms. */
const armedTimers = new WeakMap();

function armedConfirm(btn, armedLabel) {
  if (btn.dataset.armed === "true") {
    window.clearTimeout(armedTimers.get(btn));
    delete btn.dataset.armed;
    btn.textContent = btn.dataset.originalLabel || btn.textContent;
    return true;
  }
  btn.dataset.originalLabel = btn.textContent.trim();
  btn.dataset.armed = "true";
  btn.textContent = armedLabel;
  armedTimers.set(
    btn,
    window.setTimeout(() => {
      delete btn.dataset.armed;
      btn.textContent = btn.dataset.originalLabel;
    }, 4000),
  );
  return false;
}

/* ————— Auth status ————— */

function renderAuthStatus(auth) {
  const section = document.getElementById("auth-section");
  const el = document.getElementById("auth-status");
  if (!auth) {
    if (el) el.textContent = "Auth status unavailable";
    if (section) section.dataset.ready = "false";
    setStatus("offline", "offline");
    return;
  }
  if (auth.auth_mode === "chatgpt_oauth") {
    const who = [auth.chatgpt_email, auth.chatgpt_plan].filter(Boolean).join(" · ");
    if (el) el.textContent = who ? `Signed in as ${who}` : "Signed in with ChatGPT";
    if (section) section.dataset.ready = "true";
    setStatus("ready", "chatgpt oauth");
    return;
  }
  if (auth.auth_mode === "api_key") {
    if (el) el.textContent = "Using OpenAI API key";
    if (section) section.dataset.ready = "true";
    setStatus("ready", "api key");
    return;
  }
  if (el) {
    el.textContent = "Not signed in — connect ChatGPT or an API key to process documents";
  }
  if (section) section.dataset.ready = "false";
  setStatus("need-auth", "auth needed");
}

async function refreshAuth() {
  const data = await api("/api/auth/status");
  renderAuthStatus(data);
  setText("auth-out", {
    auth_mode: data.auth_mode,
    openai_ready: data.openai_ready,
    chatgpt_email: data.chatgpt_email,
    chatgpt_plan: data.chatgpt_plan,
    codex_home: data.codex_home,
  });
  return data;
}

async function refreshHealth() {
  try {
    const data = await api("/api/health");
    if (data.auth) {
      renderAuthStatus(data.auth);
    } else {
      setStatus("ready", data.status === "ok" ? "online" : "degraded");
    }
  } catch {
    setStatus("offline", "offline");
  }
}
