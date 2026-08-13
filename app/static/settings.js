import {
  api,
  applyCloudDisclaimerStatus,
  armedConfirm,
  cloudDisclaimerAccepted,
  escapeHtml,
  lastProvider,
  ollamaRemoteMode,
  refreshAuth,
  refreshCloudDisclaimer,
  refreshHealth,
  refreshOllamaStatus,
  renderOllamaStatus,
  setProviderUi,
  setText,
  toast,
} from "./api.js";
import { hooks, setKnownCategories, workflowState } from "./state.js";
import { renderWorkflow } from "./events.js";

let oauthState = null;
let oauthPollTimer = null;

function stopOauthPoll() {
  if (oauthPollTimer) {
    clearInterval(oauthPollTimer);
    oauthPollTimer = null;
  }
}

function showAuthDetails(show) {
  const details = document.getElementById("auth-details");
  const toggle = document.getElementById("auth-toggle");
  if (!details || !toggle) return;
  details.classList.toggle("hidden", !show);
  toggle.textContent = show ? "Hide options" : "More options";
}

async function selectCloudProvider() {
  setProviderUi("openai");
  await refreshCloudDisclaimer();
  if (!cloudDisclaimerAccepted) {
    document.getElementById("cloud-disclaimer-accept")?.focus();
    toast("Approve the cloud processing disclaimer to use ChatGPT or an API key", "warn");
    return;
  }
  try {
    await api("/api/llm/provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "openai" }),
    });
    toast("Switched to ChatGPT / OpenAI", "ok");
    await refreshHealth();
    await refreshAuth();
  } catch (err) {
    toast(String(err.message || err), "error");
  }
}

async function selectOllamaProvider({ enable = true, pullMissing = false, remote = false } = {}) {
  setProviderUi("ollama", { remoteOllama: remote });
  if (remote) {
    await refreshCloudDisclaimer();
    if (!cloudDisclaimerAccepted) {
      document.getElementById("cloud-disclaimer-accept")?.focus();
      toast("Approve the privacy disclaimer before using Remote Ollama", "warn");
      return;
    }
  }
  try {
    if (enable) {
      const payload = { pull_missing: pullMissing };
      if (remote) {
        const url = document.getElementById("ollama-base-url")?.value?.trim();
        if (!url) {
          toast("Enter a remote Ollama base URL", "warn");
          document.getElementById("ollama-base-url")?.focus();
          return;
        }
        payload.base_url = url;
        payload.allow_remote = true;
      } else {
        payload.base_url = "http://localhost:11434";
        payload.allow_remote = false;
      }
      const data = await api("/api/ollama/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      renderOllamaStatus(data.ollama);
      if (data.ollama?.ready) {
        toast(remote ? "Using remote Ollama" : "Using local Ollama", "ok");
      } else if (data.ollama?.reachable && data.ollama?.missing_models?.length) {
        toast("Ollama enabled — pull the required models next", "warn");
      } else if (!data.ollama?.reachable) {
        toast(data.ollama?.error || "Ollama is not reachable", "warn");
      } else {
        toast("Ollama enabled", "ok");
      }
    } else {
      await refreshOllamaStatus();
    }
    await refreshHealth();
  } catch (err) {
    toast(String(err.message || err), "error");
  }
}

function setSetupStatus(message, tone = "") {
  const el = document.getElementById("setup-status");
  if (!el) return;
  el.textContent = message || "";
  if (tone) el.dataset.tone = tone;
  else delete el.dataset.tone;
}

function setDangerStatus(message, tone = "") {
  const el = document.getElementById("danger-status");
  if (!el) return;
  el.textContent = message || "";
  if (tone) el.dataset.tone = tone;
  else delete el.dataset.tone;
}

function categoryRowHtml(cat = { name: "", folder: "" }) {
  return `<div class="cat-row">
    <label class="field">
      <span>Name</span>
      <input type="text" class="cat-name" value="${escapeHtml(cat.name || "")}" placeholder="invoice" />
    </label>
    <label class="field grow">
      <span>Folder</span>
      <input type="text" class="cat-folder" value="${escapeHtml(cat.folder || "")}" placeholder="/path/to/archive/invoice" />
    </label>
    <button type="button" class="btn ghost compact cat-remove">Remove</button>
  </div>`;
}

function renderCategories(categories) {
  const root = document.getElementById("setup-categories");
  if (!root) return;
  const list = categories && categories.length ? categories : [{ name: "other", folder: "" }];
  root.innerHTML = list.map((c) => categoryRowHtml(c)).join("");
}

function collectSetupPayload() {
  const source_dir = document.getElementById("setup-source").value.trim();
  const rows = [...document.querySelectorAll("#setup-categories .cat-row")];
  const categories = rows
    .map((row) => ({
      name: row.querySelector(".cat-name")?.value.trim() || "",
      folder: row.querySelector(".cat-folder")?.value.trim() || "",
    }))
    .filter((c) => c.name || c.folder);
  const poll_interval_seconds = Number(
    document.getElementById("setup-poll-interval").value || 0,
  );
  const require_approval = document.getElementById("setup-require-approval").checked;
  const ocr_mode = document.getElementById("setup-ocr-mode")?.value || "balanced";
  return {
    source_dir,
    categories,
    batch: { poll_interval_seconds },
    review: { require_approval },
    ocr: { mode: ocr_mode },
  };
}

function applySettingsToForm(settings) {
  document.getElementById("setup-source").value = settings.source_dir || "";
  renderCategories(settings.categories || []);
  setKnownCategories((settings.categories || []).map((c) => c.name).filter(Boolean));
  const batch = settings.batch || {};
  const interval = batch.poll_interval_seconds ?? batch.delay_seconds ?? 30;
  document.getElementById("setup-poll-interval").value = interval;
  document.getElementById("setup-require-approval").checked =
    (settings.review || {}).require_approval ?? true;
  const ocrMode = (settings.ocr || {}).mode || "balanced";
  const ocrSelect = document.getElementById("setup-ocr-mode");
  if (ocrSelect) ocrSelect.value = ocrMode;
  const catCount = (settings.categories || []).length;
  const scan =
    Number(interval) > 0 ? `scans every ${interval}s` : "manual scan only";
  setSetupStatus(
    `Source: ${settings.source_dir || "—"} · ${catCount} categor${catCount === 1 ? "y" : "ies"} · ${scan} · OCR ${ocrMode}`,
    "ok",
  );
}

export async function refreshSetup() {
  const data = await api("/api/settings");
  applySettingsToForm(data.settings || {});
  return data.settings;
}

const THEME_PRESETS = ["graphite", "carbon", "slate", "paper"];

function applyTheme(name) {
  const theme = THEME_PRESETS.includes(name) ? name : "graphite";
  if (theme === "graphite") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
  try {
    localStorage.setItem("pa-theme", theme);
  } catch (_err) {
    // private mode — theme just won't persist
  }
  for (const btn of document.querySelectorAll(".theme-card")) {
    btn.classList.toggle("active", btn.dataset.themePreset === theme);
  }
}

export function initTheme() {
  const param = new URLSearchParams(window.location.search).get("theme");
  let stored = null;
  try {
    stored = localStorage.getItem("pa-theme");
  } catch (_err) {
    // ignore
  }
  applyTheme(param || stored || "graphite");
}

export function applyMockScene() {
  // Frozen mid-run workflow so the inbox screenshot shows the pipeline alive.
  const scene = window.PA_MOCK.workflow;
  workflowState.activeFilename = scene.activeFilename;
  workflowState.activeFileId = scene.activeFileId || "demo-f2";
  workflowState.jobTotal = scene.jobTotal;
  workflowState.stepStatus = { ...scene.stepStatus };
  workflowState.stepDetail = { ...scene.stepDetail };
  workflowState.stepStartedAt = {};
  if (scene.stepStatus?.extract === "running") {
    workflowState.stepStartedAt.extract = performance.now() - 47_000;
  }
  workflowState.queue = scene.queue.map((q) => ({ ...q }));
  renderWorkflow();

  // Prefill the Ask view with a canned question and answer.
  const question = document.getElementById("question");
  if (question) question.value = "Which invoices did I get from Acme this quarter?";
  hooks.renderAskResult(window.PA_MOCK.ask);
}

function setUpdateStatus(message, tone = "") {
  const el = document.getElementById("update-status");
  if (!el) return;
  el.textContent = message || "";
  if (tone) el.dataset.tone = tone;
  else delete el.dataset.tone;
}

function showUpdateButtons({ apply = false, restart = false } = {}) {
  document.getElementById("update-apply").classList.toggle("hidden", !apply);
  document.getElementById("update-restart").classList.toggle("hidden", !restart);
}

export async function refreshUpdateVersion() {
  const data = await api("/api/update/status");
  setUpdateStatus(`PaperlessAgent v${data.current_version}`);
  const link = document.getElementById("update-repo-link");
  if (link && data.repo) link.href = `https://github.com/${data.repo}`;
}

async function waitForServerBack(timeoutMs = 45000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await new Promise((r) => setTimeout(r, 1200));
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (res.ok) return true;
    } catch (_err) {
      // still restarting
    }
  }
  return false;
}

function renderAutostartStatus(autostart) {
  const section = document.getElementById("autostart-section");
  const statusEl = document.getElementById("autostart-status");
  const hintEl = document.getElementById("autostart-hint");
  const toggle = document.getElementById("autostart-toggle");
  if (!section || !statusEl || !toggle) return;

  if (!autostart?.supported) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  toggle.disabled = Boolean(autostart.error);
  toggle.checked = Boolean(autostart.enabled);

  if (autostart.error) {
    statusEl.textContent = autostart.error;
    statusEl.dataset.tone = "warn";
    if (hintEl) {
      hintEl.textContent =
        autostart.install_hint ||
        "Fix the install path or virtualenv, then try again.";
    }
    return;
  }

  delete statusEl.dataset.tone;
  const runState = autostart.active ? "running" : "stopped";
  const bootState = autostart.enabled ? "enabled at boot" : "not enabled at boot";
  statusEl.textContent = `Service ${runState} · ${bootState} · ${autostart.url || ""}`;

  if (hintEl) {
    const lingerBit = autostart.linger
      ? "User lingering is enabled so the service can start before login."
      : "Enabling autostart also turns on user lingering for boot-time startup.";
    hintEl.textContent = `${lingerBit} Unit file: ${autostart.unit_path || "—"}`;
  }
}

export async function refreshAutostart() {
  const data = await api("/api/autostart/status");
  renderAutostartStatus(data.autostart);
  return data.autostart;
}

export function initSettings() {
  document.getElementById("cloud-disclaimer-accept")?.addEventListener("change", async (event) => {
    const checked = Boolean(event.target?.checked);
    try {
      const data = await api("/api/privacy/cloud-disclaimer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted: checked }),
      });
      applyCloudDisclaimerStatus(data.cloud_disclaimer);
      if (checked) {
        toast("Cloud processing approved — you can sign in or save an API key", "ok");
        // Persist cloud provider only after explicit approval.
        if (lastProvider !== "ollama") {
          await api("/api/llm/provider", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider: "openai" }),
          }).catch(() => {});
          await refreshHealth();
          await refreshAuth();
        }
      } else {
        toast("Cloud sign-in and API keys are locked until you approve again", "warn");
        showAuthDetails(false);
      }
    } catch (err) {
      event.target.checked = !checked;
      toast(String(err.message || err), "error");
    }
  });

  document.getElementById("provider-cloud")?.addEventListener("click", () => {
    selectCloudProvider();
  });

  document.getElementById("provider-ollama")?.addEventListener("click", () => {
    selectOllamaProvider({ enable: true, pullMissing: false, remote: false });
  });

  document.getElementById("provider-ollama-remote")?.addEventListener("click", () => {
    selectOllamaProvider({ enable: false, remote: true });
  });

  document.getElementById("ollama-enable")?.addEventListener("click", () => {
    selectOllamaProvider({ enable: true, pullMissing: false, remote: ollamaRemoteMode });
  });

  document.getElementById("ollama-start")?.addEventListener("click", async () => {
    const startBtn = document.getElementById("ollama-start");
    const statusEl = document.getElementById("ollama-status");
    try {
      if (startBtn) startBtn.disabled = true;
      if (statusEl) {
        statusEl.textContent = "Starting Ollama…";
        statusEl.dataset.tone = "warn";
      }
      const data = await api("/api/ollama/start", { method: "POST" });
      renderOllamaStatus(data.ollama);
      if (data.already_running) {
        toast("Ollama was already running", "ok");
      } else if (data.ollama?.reachable) {
        toast("Ollama started", "ok");
        if (data.ollama?.missing_models?.length) {
          toast("Pull the required models next", "warn");
        }
      } else {
        toast("Start finished, but Ollama is still unreachable", "warn");
      }
      await refreshHealth();
    } catch (err) {
      toast(String(err.message || err), "error");
      refreshOllamaStatus().catch(() => {});
    }
  });

  document.getElementById("ollama-pull")?.addEventListener("click", async () => {
    const pullBtn = document.getElementById("ollama-pull");
    const statusEl = document.getElementById("ollama-status");
    try {
      if (pullBtn) pullBtn.disabled = true;
      if (statusEl) {
        statusEl.textContent = "Pulling models via Ollama — this can take a few minutes…";
        statusEl.dataset.tone = "warn";
      }
      // Ensure provider is ollama first, then pull whatever is still missing.
      const payload = { pull_missing: true };
      if (ollamaRemoteMode) {
        const url = document.getElementById("ollama-base-url")?.value?.trim();
        if (!url) {
          toast("Enter a remote Ollama base URL", "warn");
          return;
        }
        payload.base_url = url;
        payload.allow_remote = true;
      } else {
        payload.base_url = "http://localhost:11434";
      }
      const enabled = await api("/api/ollama/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      renderOllamaStatus(enabled.ollama);
      if (enabled.ollama?.ready) {
        toast("Required Ollama models are ready", "ok");
      } else if (enabled.ollama?.missing_models?.length) {
        toast(`Still missing: ${enabled.ollama.missing_models.join(", ")}`, "warn");
      } else {
        toast("Model pull finished", "ok");
      }
      await refreshHealth();
    } catch (err) {
      toast(String(err.message || err), "error");
      refreshOllamaStatus().catch(() => {});
    } finally {
      if (pullBtn) pullBtn.disabled = false;
    }
  });

  document.getElementById("ollama-refresh")?.addEventListener("click", () => {
    refreshOllamaStatus().catch((err) => toast(String(err.message || err), "error"));
  });

  document.getElementById("ollama-unload")?.addEventListener("click", async () => {
    const btn = document.getElementById("ollama-unload");
    try {
      if (btn) btn.disabled = true;
      const data = await api("/api/ollama/unload", { method: "POST" });
      toast(
        data.unloaded?.length
          ? `Unloaded: ${data.unloaded.join(", ")}`
          : "Unload requested",
        "ok",
      );
      await refreshOllamaStatus();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById("ollama-restart")?.addEventListener("click", async () => {
    const btn = document.getElementById("ollama-restart");
    if (!window.confirm("Restart Ollama? Active processing will be blocked unless you force-cancel first.")) {
      return;
    }
    try {
      if (btn) btn.disabled = true;
      const data = await api("/api/ollama/restart", {
        method: "POST",
        body: JSON.stringify({ force: false }),
      });
      toast(data.method ? `Ollama restarted (${data.method})` : "Ollama restarted", "ok");
      await refreshOllamaStatus();
    } catch (err) {
      const msg = String(err.message || err);
      if (msg.includes("being processed") && window.confirm("Cancel the active file and restart Ollama?")) {
        try {
          const forced = await api("/api/ollama/restart", {
            method: "POST",
            body: JSON.stringify({ force: true }),
          });
          toast(forced.method ? `Ollama restarted (${forced.method})` : "Ollama restarted", "ok");
          await refreshOllamaStatus();
        } catch (forceErr) {
          toast(String(forceErr.message || forceErr), "error");
        }
      } else {
        toast(msg, "error");
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById("auth-toggle").addEventListener("click", () => {
    const details = document.getElementById("auth-details");
    showAuthDetails(details.classList.contains("hidden"));
  });

  document.getElementById("oauth-start").addEventListener("click", async () => {
    if (!cloudDisclaimerAccepted) {
      toast("Approve the cloud processing disclaimer first", "warn");
      return;
    }
    stopOauthPoll();
    showAuthDetails(true);
    setText("auth-out", "Starting ChatGPT OAuth…");
    try {
      const data = await api("/api/auth/openai/start", { method: "POST" });
      oauthState = data.state;
      document.getElementById("oauth-panel").classList.remove("hidden");
      document.getElementById("oauth-hint").textContent = data.hint || "";
      const link = document.getElementById("oauth-link");
      const authorizeUrl = data.authorize_url || "";
      if (!/^https:\/\//i.test(authorizeUrl)) {
        throw new Error("OAuth authorize URL rejected (expected https)");
      }
      link.href = authorizeUrl;
      window.open(authorizeUrl, "_blank", "noopener");
      setText("auth-out", {
        message: "Browser login opened. Waiting for callback…",
        callback_ready: data.callback_ready,
        callback_error: data.callback_error,
        redirect_uri: data.redirect_uri,
      });
      oauthPollTimer = setInterval(async () => {
        if (!oauthState) return;
        try {
          const poll = await api(
            `/api/auth/openai/poll?state=${encodeURIComponent(oauthState)}`,
          );
          if (poll.status === "success") {
            stopOauthPoll();
            document.getElementById("oauth-panel").classList.add("hidden");
            setText("auth-out", poll);
            toast("Signed in with ChatGPT", "ok");
            await refreshAuth();
          } else if (poll.status === "error") {
            stopOauthPoll();
            setText("auth-out", poll);
          }
        } catch (err) {
          stopOauthPoll();
          setText("auth-out", String(err.message || err));
        }
      }, 1500);
    } catch (err) {
      setText("auth-out", String(err.message || err));
      toast(String(err.message || err), "error");
    }
  });

  document.getElementById("oauth-complete").addEventListener("click", async () => {
    if (!oauthState) {
      setText("auth-out", "Start sign-in first.");
      return;
    }
    const callback = document.getElementById("oauth-paste").value.trim();
    if (!callback) {
      setText("auth-out", "Paste the callback URL or code first.");
      return;
    }
    try {
      const data = await api("/api/auth/openai/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: oauthState, callback }),
      });
      stopOauthPoll();
      document.getElementById("oauth-panel").classList.add("hidden");
      setText("auth-out", data);
      toast("Signed in with ChatGPT", "ok");
      await refreshAuth();
    } catch (err) {
      setText("auth-out", String(err.message || err));
      toast(String(err.message || err), "error");
    }
  });

  document.getElementById("auth-refresh").addEventListener("click", () => {
    refreshAuth().catch((err) => setText("auth-out", String(err.message || err)));
  });

  document.getElementById("auth-logout").addEventListener("click", async () => {
    stopOauthPoll();
    try {
      const data = await api("/api/auth/logout", { method: "POST" });
      setText("auth-out", data);
      toast("Logged out", "ok");
      await refreshAuth();
    } catch (err) {
      setText("auth-out", String(err.message || err));
      toast(String(err.message || err), "error");
    }
  });

  document.getElementById("api-key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!cloudDisclaimerAccepted) {
      toast("Approve the cloud processing disclaimer first", "warn");
      return;
    }
    const api_key = document.getElementById("api-key").value.trim();
    if (!api_key) return;
    try {
      const data = await api("/api/auth/api-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key }),
      });
      document.getElementById("api-key").value = "";
      setText("auth-out", data);
      toast("API key saved", "ok");
      await refreshAuth();
    } catch (err) {
      setText("auth-out", String(err.message || err));
      toast(String(err.message || err), "error");
    }
  });

  document.getElementById("setup-add-category").addEventListener("click", () => {
    const root = document.getElementById("setup-categories");
    root.insertAdjacentHTML("beforeend", categoryRowHtml({ name: "", folder: "" }));
  });

  document.getElementById("setup-categories").addEventListener("click", (e) => {
    const btn = e.target.closest(".cat-remove");
    if (!btn) return;
    const rows = document.querySelectorAll("#setup-categories .cat-row");
    if (rows.length <= 1) {
      setSetupStatus("Keep at least one category (including other).", "warn");
      return;
    }
    btn.closest(".cat-row")?.remove();
  });

  document.getElementById("setup-save").addEventListener("click", async () => {
    const btn = document.getElementById("setup-save");
    btn.disabled = true;
    setSetupStatus("Saving setup…");
    try {
      const payload = collectSetupPayload();
      const data = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      applySettingsToForm(data.settings || payload);
      setSetupStatus("Setup saved.", "ok");
      toast("Setup saved", "ok");
    } catch (err) {
      setSetupStatus(String(err.message || err), "err");
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("theme-grid").addEventListener("click", (e) => {
    const btn = e.target.closest(".theme-card");
    if (!btn) return;
    applyTheme(btn.dataset.themePreset);
  });

  document.getElementById("mock-toggle").addEventListener("change", (e) => {
    window.PA_MOCK?.setEnabled(e.target.checked);
    const url = new URL(window.location.href);
    if (url.searchParams.has("mock")) {
      // Drop the ?mock= override so the stored preference takes effect.
      url.searchParams.delete("mock");
      window.location.href = url.toString();
    } else {
      // Same-URL navigation with a hash would not reload; force it.
      window.location.reload();
    }
  });

  document.getElementById("update-check").addEventListener("click", async () => {
    const btn = document.getElementById("update-check");
    btn.disabled = true;
    setUpdateStatus("Checking GitHub for updates…");
    showUpdateButtons({});
    document.getElementById("update-notes").classList.add("hidden");
    try {
      const data = await api("/api/update/status?check=true");
      if (data.status !== "success") {
        setUpdateStatus(data.error || "Update check failed", "err");
        return;
      }
      if (data.update_available) {
        if (data.verifiable) {
          setUpdateStatus(
            `Update available: v${data.current_version} → v${data.latest_version} (SHA-256 verified)`,
            "warn",
          );
          showUpdateButtons({ apply: true });
        } else {
          setUpdateStatus(
            data.verification_error ||
              `v${data.latest_version} is available but has no SHA-256 release assets — install refused`,
            "err",
          );
          showUpdateButtons({});
        }
        if (data.notes) {
          const notes = document.getElementById("update-notes");
          notes.textContent = data.notes;
          notes.classList.remove("hidden");
        }
      } else {
        setUpdateStatus(
          data.message || `PaperlessAgent v${data.current_version} — up to date`,
          "ok",
        );
      }
    } catch (err) {
      setUpdateStatus(String(err.message || err), "err");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("update-apply").addEventListener("click", async () => {
    const btn = document.getElementById("update-apply");
    btn.disabled = true;
    setUpdateStatus("Downloading and installing update…");
    try {
      const data = await api("/api/update/apply", { method: "POST" });
      setUpdateStatus(
        `Installed v${data.installed_version} (${data.updated_count} files, checksum ok) — restart to finish`,
        "ok",
      );
      toast(`Updated to v${data.installed_version}`, "ok");
      showUpdateButtons({ restart: true });
    } catch (err) {
      setUpdateStatus(String(err.message || err), "err");
      toast(String(err.message || err), "error");
      btn.disabled = false;
    }
  });

  document.getElementById("update-restart").addEventListener("click", async () => {
    const btn = document.getElementById("update-restart");
    btn.disabled = true;
    setUpdateStatus("Restarting PaperlessAgent…");
    try {
      await api("/api/update/restart", { method: "POST" });
    } catch (_err) {
      // The connection may drop mid-request while the process re-execs.
    }
    const back = await waitForServerBack();
    if (back) {
      toast("Restarted — reloading", "ok");
      window.location.reload();
    } else {
      setUpdateStatus("Server did not come back — restart it manually", "err");
      btn.disabled = false;
    }
  });

  document.getElementById("clear-all-data").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    if (!armedConfirm(btn, "Really delete everything?")) return;
    btn.disabled = true;
    try {
      const data = await api("/api/data", {
        method: "DELETE",
        body: { confirmation: "DELETE ALL PAPERLESSAGENT DATA" },
      });
      setDangerStatus(data.message || "All stored data removed.", "ok");
      toast("All stored data removed", "ok");
      hooks.refreshDocs().catch(() => {});
      hooks.refreshInbox().catch(() => {});
    } catch (err) {
      const msg = String(err.message || err);
      setDangerStatus(msg, "error");
      toast(msg, "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("autostart-toggle")?.addEventListener("change", async (e) => {
    const toggle = e.target;
    const enabled = toggle.checked;
    toggle.disabled = true;
    try {
      const data = await api("/api/autostart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      renderAutostartStatus(data.autostart);
      toast(
        enabled
          ? "PaperlessAgent will start automatically at boot."
          : "Boot autostart disabled.",
        "success",
      );
    } catch (err) {
      toggle.checked = !enabled;
      toast(String(err.message || err), "error");
      await refreshAutostart().catch(() => {});
    } finally {
      toggle.disabled = false;
    }
  });
}

export { setSetupStatus };
