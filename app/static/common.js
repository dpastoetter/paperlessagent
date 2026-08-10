async function api(path, options = {}) {
  // Mockup mode (Settings → Appearance): serve canned demo data, block writes.
  if (window.PA_MOCK?.enabled) {
    return window.PA_MOCK.respond(path, options);
  }
  const headers = new Headers(options.headers || {});
  // Custom header required by the server on mutating routes — blocks CSRF
  // from cross-site form posts (browsers cannot attach it without CORS).
  headers.set("X-Requested-With", "PaperlessAgent");
  const res = await fetch(path, { ...options, headers });
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
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

/* ————— Provider / auth status ————— */

let lastProvider = "";
let cloudDisclaimerAccepted = false;

function setCloudAuthLocked(locked) {
  const panel = document.getElementById("cloud-auth-panel");
  const checkbox = document.getElementById("cloud-disclaimer-accept");
  if (panel) panel.dataset.locked = locked ? "true" : "false";
  if (checkbox) checkbox.checked = !locked;
  for (const id of ["oauth-start", "auth-toggle"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  }
  const apiKey = document.getElementById("api-key");
  const apiKeyForm = document.getElementById("api-key-form");
  const saveBtn = apiKeyForm?.querySelector('button[type="submit"]');
  if (apiKey) apiKey.disabled = locked;
  if (saveBtn) saveBtn.disabled = locked;
  if (locked) {
    const details = document.getElementById("auth-details");
    const toggle = document.getElementById("auth-toggle");
    if (details) details.classList.add("hidden");
    if (toggle) toggle.textContent = "More options";
  }
}

function applyCloudDisclaimerStatus(status) {
  cloudDisclaimerAccepted = Boolean(status?.accepted);
  const box = document.getElementById("cloud-disclaimer");
  if (box) box.dataset.accepted = cloudDisclaimerAccepted ? "true" : "false";
  setCloudAuthLocked(!cloudDisclaimerAccepted);
  return cloudDisclaimerAccepted;
}

async function refreshCloudDisclaimer() {
  try {
    const data = await api("/api/privacy/cloud-disclaimer");
    return applyCloudDisclaimerStatus(data.cloud_disclaimer);
  } catch {
    setCloudAuthLocked(true);
    return false;
  }
}

function setProviderUi(provider) {
  lastProvider = provider || "";
  const section = document.getElementById("auth-section");
  const ollamaPanel = document.getElementById("ollama-panel");
  const cloudPanel = document.getElementById("cloud-auth-panel");
  const cloudBtn = document.getElementById("provider-cloud");
  const ollamaBtn = document.getElementById("provider-ollama");
  const isOllama = provider === "ollama";

  if (section) section.dataset.provider = provider || "";
  if (ollamaPanel) ollamaPanel.classList.toggle("hidden", !isOllama);
  if (cloudPanel) cloudPanel.classList.toggle("hidden", isOllama);
  if (cloudBtn) cloudBtn.dataset.active = isOllama ? "false" : "true";
  if (ollamaBtn) ollamaBtn.dataset.active = isOllama ? "true" : "false";
}

function renderOllamaStatus(ollama) {
  const statusEl = document.getElementById("ollama-status");
  const hintEl = document.getElementById("ollama-hint");
  const section = document.getElementById("auth-section");
  const pullBtn = document.getElementById("ollama-pull");
  if (!ollama) {
    if (statusEl) statusEl.textContent = "Ollama status unavailable";
    return;
  }

  if (!ollama.reachable) {
    if (statusEl) {
      statusEl.textContent =
        ollama.error || "Ollama is not running on this machine";
      statusEl.dataset.tone = "warn";
    }
    if (hintEl) {
      hintEl.textContent =
        ollama.install_hint ||
        "Install Ollama, start it, then click Use Ollama.";
    }
    if (section) section.dataset.ready = "false";
    if (pullBtn) pullBtn.disabled = true;
    setStatus("need-auth", "ollama offline");
    return;
  }

  if (pullBtn) pullBtn.disabled = !(ollama.missing_models || []).length;

  if (ollama.missing_models?.length) {
    if (statusEl) {
      statusEl.textContent = `Ollama is running — missing models: ${ollama.missing_models.join(", ")}`;
      statusEl.dataset.tone = "warn";
    }
    if (hintEl) {
      hintEl.textContent = ollama.pull_command
        ? `Run: ${ollama.pull_command}`
        : "Pull the required models, then refresh.";
    }
    if (section) section.dataset.ready = ollama.active ? "false" : section.dataset.ready;
    setStatus("need-auth", "ollama · models needed");
    return;
  }

  const chatLabel = ollama.resolved_chat_model || ollama.chat_model;
  const embedLabel = ollama.resolved_embedding_model || ollama.embedding_model;
  if (statusEl) {
    statusEl.textContent = ollama.active
      ? `Using local Ollama · ${chatLabel} + ${embedLabel}`
      : `Ollama ready · ${chatLabel} + ${embedLabel}`;
    statusEl.dataset.tone = "ok";
  }
  if (hintEl) {
    hintEl.textContent = ollama.active
      ? "Documents stay on this machine — no cloud sign-in needed."
      : "Click Use Ollama to switch the app to local models.";
  }
  if (ollama.active) {
    const authLine = document.getElementById("auth-status");
    if (authLine) {
      authLine.textContent = `Local Ollama · ${chatLabel}`;
      authLine.dataset.tone = "ok";
    }
    if (section) section.dataset.ready = "true";
    setStatus("ready", `ollama · ${ollama.chat_model || "local"}`);
  }
}

function renderAuthStatus(auth) {
  if (lastProvider === "ollama") {
    // Cloud auth panel is inactive while Ollama is selected.
    return;
  }
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
    if (el) {
      el.textContent = who ? `Signed in as ${who}` : "Signed in with ChatGPT";
      el.dataset.tone = "ok";
    }
    if (section) section.dataset.ready = "true";
    setStatus("ready", "chatgpt oauth");
    return;
  }
  if (auth.auth_mode === "api_key") {
    if (el) {
      el.textContent = "Using OpenAI API key";
      el.dataset.tone = "ok";
    }
    if (section) section.dataset.ready = "true";
    setStatus("ready", "api key");
    return;
  }
  if (el) {
    el.textContent = "Not signed in — connect ChatGPT or an API key, or switch to Local Ollama";
    delete el.dataset.tone;
  }
  if (section) section.dataset.ready = "false";
  setStatus("need-auth", "auth needed");
}

async function refreshAuth() {
  if (lastProvider === "ollama") {
    return null;
  }
  const data = await api("/api/auth/status");
  if (data.cloud_disclaimer) {
    applyCloudDisclaimerStatus(data.cloud_disclaimer);
  }
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

async function refreshOllamaStatus() {
  const data = await api("/api/ollama/status");
  renderOllamaStatus(data.ollama);
  return data.ollama;
}

async function refreshHealth() {
  try {
    const data = await api("/api/health");
    setProviderUi(data.llm_provider || "");
    if (data.cloud_disclaimer) {
      applyCloudDisclaimerStatus(data.cloud_disclaimer);
    }
    if (data.llm_provider === "ollama") {
      if (data.ollama) {
        renderOllamaStatus(data.ollama);
      } else {
        await refreshOllamaStatus();
      }
      return data;
    }
    if (data.auth) {
      renderAuthStatus(data.auth);
    } else {
      setStatus("ready", data.status === "ok" ? "online" : "degraded");
    }
    return data;
  } catch {
    setStatus("offline", "offline");
    return null;
  }
}
