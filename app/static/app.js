/* ————— Hash router ————— */

const VIEWS = ["inbox", "review", "archive", "ask", "settings"];

function currentView() {
  const name = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return VIEWS.includes(name) ? name : "inbox";
}

function renderRoute() {
  const view = currentView();
  for (const el of document.querySelectorAll(".view")) {
    el.classList.toggle("active", el.dataset.view === view);
  }
  for (const el of document.querySelectorAll(".nav-item")) {
    el.classList.toggle("active", el.dataset.view === view);
  }
  document.title = `${view[0].toUpperCase()}${view.slice(1)} · PaperlessAgent`;
}

window.addEventListener("hashchange", renderRoute);

/* ————— Inbox ————— */

function setProcessStatus(message, tone = "") {
  const el = document.getElementById("process-status");
  if (!el) return;
  el.textContent = message || "";
  if (tone) el.dataset.tone = tone;
  else delete el.dataset.tone;
}

function renderInboxSummary(data) {
  const el = document.getElementById("inbox-summary");
  if (!el) return;
  const files = data.files || [];
  const count = data.count ?? files.length;
  delete el.dataset.tone;
  if (!count) {
    el.textContent = "Inbox is empty";
    return;
  }
  const names = files.map((f) => f.name).join(", ");
  el.textContent = `Inbox: ${count} file${count === 1 ? "" : "s"} — ${names}`;
  el.dataset.tone = "ok";
}

async function refreshInbox() {
  const data = await api("/api/inbox");
  setText("inbox-out", data);
  renderInboxSummary(data);
  return data;
}

/* ————— Review queue ————— */

let knownCategories = [];

function updateReviewBadge(count) {
  const badge = document.getElementById("review-badge");
  if (!badge) return;
  badge.textContent = String(count);
  badge.classList.toggle("hidden", !count);
}

function duplicateNoticeHtml(duplicates) {
  if (!duplicates || !duplicates.length) return "";
  const KIND_LABELS = {
    exact: "identical file already archived",
    content: "identical content already archived",
    similar: "very similar document archived",
    pending: "same file already waiting for review",
  };
  const items = duplicates
    .map((d) => {
      const label = KIND_LABELS[d.kind] || d.kind;
      const score =
        d.kind === "similar" && typeof d.score === "number"
          ? ` (${Math.round(d.score * 100)}% match)`
          : "";
      const name = d.filename ? ` — ${d.filename}` : "";
      return `<li>${escapeHtml(label)}${escapeHtml(score)}${escapeHtml(name)}</li>`;
    })
    .join("");
  return `<div class="dup-warning" role="alert">
    <svg class="icon" aria-hidden="true"><use href="#i-alert" /></svg>
    <div>
      <strong>Possible duplicate</strong>
      <ul>${items}</ul>
    </div>
  </div>`;
}

function categoryOptionsHtml(selected) {
  const names = knownCategories.length ? knownCategories : [selected || "other"];
  const list = names.includes(selected) || !selected ? names : [selected, ...names];
  return list
    .map(
      (name) =>
        `<option value="${escapeHtml(name)}"${name === selected ? " selected" : ""}>${escapeHtml(name)}</option>`,
    )
    .join("");
}

function reviewCardHtml(item, index) {
  const p = item.proposal || {};
  const id = escapeHtml(item.id);
  return `<article class="review-card" data-review-id="${id}" style="animation-delay:${Math.min(index, 8) * 35}ms">
    <header class="review-head">
      <div class="review-title">
        <span class="doc-badge">${escapeHtml(p.doc_type || "other")}</span>
        <h3>${escapeHtml(item.original_name || "document")}</h3>
      </div>
      <button type="button" class="btn ghost compact review-open">
        <svg class="icon" aria-hidden="true"><use href="#i-external" /></svg>
        Open scan
      </button>
    </header>
    ${duplicateNoticeHtml(item.duplicates)}
    <div class="review-fields">
      <label class="field grow">
        <span>Filename</span>
        <input type="text" class="rv-filename" value="${escapeHtml(p.filename || "")}" />
      </label>
      <label class="field narrow">
        <span>Category</span>
        <select class="rv-doc-type">${categoryOptionsHtml(p.doc_type || "other")}</select>
      </label>
      <label class="field narrow">
        <span>Date</span>
        <input type="text" class="rv-doc-date" value="${escapeHtml(p.doc_date || "")}" placeholder="YYYY-MM-DD" />
      </label>
      <label class="field grow">
        <span>Subject</span>
        <input type="text" class="rv-subject" value="${escapeHtml(p.subject || "")}" placeholder="What the document is about" />
      </label>
      <label class="field grow">
        <span>People / organizations</span>
        <input type="text" class="rv-counterparties" value="${escapeHtml(p.counterparties || "")}" placeholder="Sender, doctor, insurer, employer…" />
      </label>
      <label class="field narrow">
        <span>Amount</span>
        <input type="number" step="0.01" class="rv-amount" value="${escapeHtml(p.amount ?? "")}" placeholder="Bills only" />
      </label>
      <label class="field narrow">
        <span>Currency</span>
        <input type="text" class="rv-currency" value="${escapeHtml(p.currency || "")}" placeholder="EUR" />
      </label>
      <label class="field full">
        <span>Summary</span>
        <textarea class="rv-summary" rows="2">${escapeHtml(p.summary || "")}</textarea>
      </label>
    </div>
    <footer class="review-actions">
      <button type="button" class="btn primary review-approve">
        <svg class="icon" aria-hidden="true"><use href="#i-check" /></svg>
        Approve &amp; file
      </button>
      <button type="button" class="btn ghost danger review-reject">Reject &amp; remove scan</button>
    </footer>
  </article>`;
}

function renderReviews(payload) {
  const root = document.getElementById("reviews");
  if (!root) return;
  const reviews = payload.reviews || [];
  updateReviewBadge(reviews.length);
  if (!reviews.length) {
    root.innerHTML = `<div class="empty-state">
      <svg class="icon" aria-hidden="true"><use href="#i-review" /></svg>
      <p>Nothing waiting for review</p>
    </div>`;
    return;
  }
  root.innerHTML = reviews.map((item, i) => reviewCardHtml(item, i)).join("");
}

async function refreshReviews() {
  const data = await api("/api/reviews");
  renderReviews(data);
  return data;
}

function collectReviewOverrides(card) {
  const amountRaw = card.querySelector(".rv-amount")?.value.trim();
  const overrides = {
    filename: card.querySelector(".rv-filename")?.value.trim() || null,
    doc_type: card.querySelector(".rv-doc-type")?.value || null,
    doc_date: card.querySelector(".rv-doc-date")?.value.trim() || null,
    subject: card.querySelector(".rv-subject")?.value.trim() || null,
    counterparties: card.querySelector(".rv-counterparties")?.value.trim() || null,
    currency: card.querySelector(".rv-currency")?.value.trim() || null,
    summary: card.querySelector(".rv-summary")?.value.trim() || null,
  };
  if (amountRaw && !Number.isNaN(Number(amountRaw))) {
    overrides.amount = Number(amountRaw);
  }
  return overrides;
}

document.getElementById("reviews").addEventListener("click", async (e) => {
  const card = e.target.closest(".review-card");
  if (!card) return;
  const reviewId = card.dataset.reviewId;
  if (!reviewId) return;

  if (e.target.closest(".review-open")) {
    const btn = e.target.closest(".review-open");
    btn.disabled = true;
    try {
      await openDocumentFile(`/api/reviews/${encodeURIComponent(reviewId)}/file`);
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
    }
    return;
  }

  if (e.target.closest(".review-approve")) {
    const btn = e.target.closest(".review-approve");
    btn.disabled = true;
    try {
      const data = await api(`/api/reviews/${encodeURIComponent(reviewId)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectReviewOverrides(card)),
      });
      toast(`Filed ${data.filename || "document"}`, "ok");
      await refreshReviews();
      refreshDocs().catch(() => {});
      refreshInbox().catch(() => {});
    } catch (err) {
      toast(String(err.message || err), "error");
      btn.disabled = false;
    }
    return;
  }

  if (e.target.closest(".review-reject")) {
    const btn = e.target.closest(".review-reject");
    if (!armedConfirm(btn, "Really reject?")) return;
    btn.disabled = true;
    try {
      await api(`/api/reviews/${encodeURIComponent(reviewId)}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_file: true }),
      });
      toast("Rejected — scan removed from inbox", "ok");
      await refreshReviews();
      refreshInbox().catch(() => {});
    } catch (err) {
      toast(String(err.message || err), "error");
      btn.disabled = false;
    }
  }
});

document.getElementById("refresh-reviews").addEventListener("click", () => {
  refreshReviews().catch((err) => toast(String(err.message || err), "error"));
});

/* ————— Archive ————— */

function docsEmptyState(message) {
  return `<div class="empty-state">
    <svg class="icon" aria-hidden="true"><use href="#i-archive" /></svg>
    <p>${escapeHtml(message)}</p>
  </div>`;
}

function renderDocs(payload) {
  const root = document.getElementById("docs");
  const docs = payload.documents || [];
  if (!docs.length) {
    root.innerHTML = docsEmptyState("No documents yet");
    return;
  }
  root.innerHTML = docs
    .map((d, i) => {
      const title = d.filename || d.original_name || d.id;
      const summary = d.summary || "No summary";
      const badge = d.doc_type || "other";
      const context = d.subject || d.counterparties || "";
      const meta = [d.doc_date || "undated", context, d.id]
        .filter(Boolean)
        .join(" · ");
      return `<article class="doc" style="animation-delay:${Math.min(i, 8) * 35}ms">
        <div class="doc-body">
          <div class="doc-title-row">
            <span class="doc-badge">${escapeHtml(badge)}</span>
            <h3>${escapeHtml(title)}</h3>
          </div>
          <p>${escapeHtml(summary)}</p>
          <p class="meta">${escapeHtml(meta)}</p>
        </div>
        <div class="doc-actions">
          <button type="button" class="btn ghost compact reveal-btn" data-doc-id="${escapeHtml(d.id)}">
            Reveal
          </button>
        </div>
      </article>`;
    })
    .join("");
}

async function refreshDocs(params = {}) {
  const qs = new URLSearchParams(params);
  const data = await api(`/api/documents?${qs.toString()}`);
  renderDocs(data);
}

document.getElementById("docs").addEventListener("click", async (e) => {
  const btn = e.target.closest(".reveal-btn");
  if (!btn) return;
  const docId = btn.dataset.docId;
  if (!docId) return;
  btn.disabled = true;
  try {
    await api(`/api/documents/${encodeURIComponent(docId)}/reveal`, { method: "POST" });
  } catch (err) {
    toast(String(err.message || err), "error");
  } finally {
    btn.disabled = false;
  }
});

/* ————— Upload ————— */

const fileInput = document.getElementById("file");
const fileLabel = document.getElementById("file-label");
fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "No file selected";
});

document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!fileInput.files.length) {
    setProcessStatus("Choose a PDF or image first", "warn");
    return;
  }
  const body = new FormData();
  body.append("file", fileInput.files[0]);
  setProcessStatus("Uploading…");
  setText("process-out", "Uploading…");
  try {
    const saved = await api("/api/upload", { method: "POST", body });
    setText("process-out", saved);
    setProcessStatus(`Uploaded ${saved.filename || "file"} → inbox`, "ok");
    toast(`Uploaded ${saved.filename || "file"}`, "ok");
    fileInput.value = "";
    fileLabel.textContent = "No file selected";
    await refreshInbox();
  } catch (err) {
    const msg = String(err.message || err);
    setText("process-out", msg);
    setProcessStatus(msg, "error");
    toast(msg, "error");
  }
});

document.getElementById("refresh-inbox").addEventListener("click", () => {
  refreshInbox().catch((err) => {
    const msg = String(err.message || err);
    setText("inbox-out", msg);
    setProcessStatus(msg, "error");
  });
});

document.getElementById("clear-inbox").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!armedConfirm(btn, "Really remove?")) return;
  try {
    const data = await api("/api/inbox", { method: "DELETE" });
    setText("process-out", data);
    const count = data.removed_count ?? 0;
    setProcessStatus(
      count ? `Removed ${count} file${count === 1 ? "" : "s"} from inbox` : "Inbox already empty",
      count ? "ok" : "warn",
    );
    await refreshInbox();
  } catch (err) {
    const msg = String(err.message || err);
    setText("process-out", msg);
    setProcessStatus(msg, "error");
    toast(msg, "error");
  }
});

function setProcessInboxBusy(busy) {
  const btn = document.getElementById("process-inbox");
  if (btn) btn.disabled = Boolean(busy);
}

document.getElementById("job-queue")?.addEventListener("click", async (ev) => {
  const cancelBtn = ev.target.closest(".queue-cancel");
  if (cancelBtn) {
    const fileId = cancelBtn.dataset.fileId;
    if (!fileId) return;
    try {
      await api("/api/process/cancel", {
        method: "POST",
        body: JSON.stringify({ file_id: fileId }),
      });
      toast("Cancellation requested", "ok");
    } catch (err) {
      toast(String(err.message || err), "error");
    }
    return;
  }
  const retryBtn = ev.target.closest(".queue-retry");
  if (retryBtn) {
    const path = retryBtn.dataset.path;
    if (!path) return;
    retryBtn.disabled = true;
    try {
      const data = await api("/api/process/retry", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      if (data.cancelled) {
        toast(data.message || "Cancelled — retry again when the job finishes", "warn");
      } else {
        toast("Retry started", "ok");
        setProcessInboxBusy(true);
      }
      await refreshInbox();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      retryBtn.disabled = false;
    }
  }
});

document.getElementById("process-inbox").addEventListener("click", async () => {
  const btn = document.getElementById("process-inbox");
  btn.disabled = true;
  setProcessStatus("Processing inbox… watch the workflow.");
  setText("process-out", "Processing inbox…");
  try {
    const data = await api("/api/process-inbox", { method: "POST" });
    setText("process-out", data);
    if (data.status === "empty" || data.processed === 0) {
      setProcessStatus(data.message || "Inbox is empty — upload a scan first", "warn");
    } else {
      const results = data.results || [];
      const errors = results.filter((r) => r.error);
      const pending = results.filter((r) => r.result?.status === "pending_review");
      const filed = results
        .filter((r) => !r.error && r.result?.result?.filename)
        .map((r) => r.result.result.filename);
      if (errors.length) {
        setProcessStatus(
          `Processed ${data.processed} file(s), ${errors.length} failed. ${errors.map((e) => e.error).join(" | ")}`,
          "error",
        );
        toast(`${errors.length} file(s) failed`, "error");
      } else if (pending.length) {
        const filedNote = filed.length ? ` Filed: ${filed.join(", ")}.` : "";
        setProcessStatus(
          `${pending.length} file(s) waiting for your approval in Review.${filedNote}`,
          "ok",
        );
        toast(`${pending.length} file(s) queued for review`, "ok");
      } else {
        setProcessStatus(
          filed.length ? `Filed: ${filed.join(", ")}` : `Processed ${data.processed} file(s)`,
          "ok",
        );
        toast(`Filed ${data.processed} file(s)`, "ok");
      }
    }
    await refreshInbox();
    await refreshDocs();
    await refreshReviews().catch(() => {});
  } catch (err) {
    const msg = String(err.message || err);
    setText("process-out", msg);
    setProcessStatus(msg, "error");
    toast(msg, "error");
  } finally {
    setProcessInboxBusy(false);
  }
});

document.getElementById("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = document.getElementById("search-q").value.trim();
  const doc_type = document.getElementById("search-type").value.trim();
  const params = {};
  if (q) params.q = q;
  if (doc_type) params.doc_type = doc_type;
  try {
    await refreshDocs(params);
  } catch (err) {
    document.getElementById("docs").innerHTML = docsEmptyState(String(err.message || err));
  }
});

/* ————— Ask ————— */

function stripSourcesSection(text) {
  return String(text || "")
    .replace(/\n*(?:#{1,3}\s*)?sources\b[\s\S]*$/i, "")
    .trim();
}

function renderAskResult(data) {
  const root = document.getElementById("ask-out");
  if (!root) return;
  const reply = stripSourcesSection(data.reply || "");
  if (!reply) {
    root.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
    return;
  }

  const sources = (data.sources || []).filter((s) => s && s.document_id);
  const sourcesHtml = sources.length
    ? `<div class="ask-sources">
        <h3>Sources</h3>
        <ul>
          ${sources
            .map((s) => {
              const id = encodeURIComponent(s.document_id);
              const name = escapeHtml(s.filename || "document");
              // Only allow same-origin API file paths — never trust open_url schemes.
              const candidate = typeof s.open_url === "string" ? s.open_url : "";
              const openUrl =
                candidate.startsWith("/api/documents/") && !candidate.includes("://")
                  ? candidate
                  : `/api/documents/${id}/file`;
              return `<li>
                <span class="source-meta">${name}</span>
                <span class="source-actions">
                  <a class="source-link" href="${escapeHtml(openUrl)}" data-open-url="${escapeHtml(openUrl)}">
                    <svg class="icon" aria-hidden="true"><use href="#i-external" /></svg>
                    Open
                  </a>
                  <button type="button" class="source-reveal" data-doc-id="${escapeHtml(s.document_id)}">
                    Reveal
                  </button>
                </span>
              </li>`;
            })
            .join("")}
        </ul>
      </div>`
    : "";

  root.innerHTML = `<div class="ask-reply">${escapeHtml(reply)}</div>${sourcesHtml}`;
}

async function openDocumentFile(url) {
  if (window.PA_MOCK?.enabled) {
    throw new Error("Mockup mode is on — files are demo data. Turn it off in Settings.");
  }
  // Open the tab synchronously while we still have the click gesture.
  // After `await fetch(...)` browsers treat window.open as a popup and either
  // block it or leave a blank tab — which is what Review "Open scan" hit.
  const win = window.open("about:blank", "_blank");
  if (!win) {
    throw new Error("Popup blocked — allow popups for this site to open documents");
  }
  try {
    win.document.title = "Loading…";
  } catch (_err) {
    // cross-origin about:blank quirks — ignore
  }
  try {
    const res = await fetch(url);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = data.detail || data.error || detail;
      } catch (_err) {
        // ignore
      }
      throw new Error(detail || "Could not open file");
    }
    const bytes = await res.arrayBuffer();
    const headerType = (res.headers.get("content-type") || "").split(";")[0].trim();
    // Review/document URLs have no .pdf suffix — sniff magic bytes so the
    // browser PDF viewer always gets application/pdf.
    const head = new Uint8Array(bytes, 0, Math.min(bytes.byteLength, 5));
    const isPdf =
      head.length >= 4 &&
      head[0] === 0x25 &&
      head[1] === 0x50 &&
      head[2] === 0x44 &&
      head[3] === 0x46; // %PDF
    const mime = isPdf
      ? "application/pdf"
      : headerType || "application/octet-stream";
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
    win.location.href = objectUrl;
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (err) {
    try {
      win.close();
    } catch (_closeErr) {
      // ignore
    }
    throw err;
  }
}

document.getElementById("ask-out").addEventListener("click", async (e) => {
  const link = e.target.closest(".source-link");
  if (link) {
    e.preventDefault();
    const url = link.dataset.openUrl || link.getAttribute("href");
    if (!url) return;
    link.setAttribute("aria-busy", "true");
    try {
      await openDocumentFile(url);
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      link.removeAttribute("aria-busy");
    }
    return;
  }

  const btn = e.target.closest(".source-reveal");
  if (!btn) return;
  const docId = btn.dataset.docId;
  if (!docId) return;
  btn.disabled = true;
  try {
    await api(`/api/documents/${encodeURIComponent(docId)}/reveal`, { method: "POST" });
  } catch (err) {
    toast(String(err.message || err), "error");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = document.getElementById("question").value.trim();
  if (!question) return;
  const root = document.getElementById("ask-out");
  root.innerHTML = `<div class="ask-reply thinking">Thinking…</div>`;
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!data.reply) {
      root.textContent = JSON.stringify(data, null, 2);
      return;
    }
    renderAskResult(data);
    refreshHealth().catch(() => {});
  } catch (err) {
    root.innerHTML = `<div class="ask-reply">${escapeHtml(String(err.message || err))}</div>`;
  }
});

/* ————— Workflow (SSE) ————— */

const DEFAULT_PIPELINE_STEPS = [
  { id: "read", label: "Open file" },
  { id: "ai_ocr", label: "Transcribe" },
  { id: "extract", label: "Find details" },
  { id: "name", label: "Name file" },
  { id: "review", label: "Review" },
  { id: "file", label: "Save" },
  { id: "index", label: "Make searchable" },
];

/** Minimum time a step stays visibly running before a terminal transition. */
const STEP_MIN_VISIBLE_MS = 180;

const workflowState = {
  steps: DEFAULT_PIPELINE_STEPS,
  stepStatus: {},
  stepDetail: {},
  stepStartedAt: {},
  elapsedTimer: null,
  stepTimers: {},
  stepAnimQueue: [],
  stepAnimRunning: false,
  stepShownAt: {},
  pipelineMountKey: "",
  queueMountKey: "",
  queue: [],
  activeFileId: null,
  activeFilename: null,
  jobTotal: 0,
  jobIndex: 0,
};

function pipelineStepsKey() {
  return workflowState.steps.map((step) => step.id).join("|");
}

function queueFilesKey() {
  return workflowState.queue.map((item) => item.file_id || "").join("|");
}

function stepStateLabel(stepId, now = performance.now()) {
  const status = workflowState.stepStatus[stepId] || "idle";
  if (status === "running" && workflowState.stepStartedAt[stepId]) {
    return formatElapsed(now - workflowState.stepStartedAt[stepId]);
  }
  if (status === "running") return "…";
  if (status === "done") return "done";
  if (status === "error") return "failed";
  if (status === "skipped") return "skip";
  if (status === "wait") return "wait";
  return "wait";
}

function ensurePipelineMounted() {
  const root = document.getElementById("pipeline");
  if (!root) return;

  const key = pipelineStepsKey();
  if (workflowState.pipelineMountKey === key && root.querySelector(".pipeline-step")) {
    return;
  }

  workflowState.pipelineMountKey = key;
  root.replaceChildren();
  workflowState.steps.forEach((step, index) => {
    const li = document.createElement("li");
    li.className = "pipeline-step";
    li.dataset.step = step.id;
    li.dataset.status = workflowState.stepStatus[step.id] || "idle";

    const node = document.createElement("div");
    node.className = "pipeline-node";

    const labelEl = document.createElement("span");
    labelEl.className = "step-label";
    labelEl.textContent = step.label;

    const stateEl = document.createElement("span");
    stateEl.className = "step-state";
    stateEl.textContent = stepStateLabel(step.id);

    node.append(labelEl, stateEl);
    li.appendChild(node);

    if (index < workflowState.steps.length - 1) {
      const connector = document.createElement("span");
      connector.className = "pipeline-connector";
      connector.setAttribute("aria-hidden", "true");
      li.appendChild(connector);
    }

    root.appendChild(li);
  });
}

function patchPipelineStep(stepEl, step, now) {
  const status = workflowState.stepStatus[step.id] || "idle";
  const detail = workflowState.stepDetail[step.id] || "";
  stepEl.dataset.status = status;
  stepEl.title = detail || step.label;
  const stateEl = stepEl.querySelector(".step-state");
  if (stateEl) stateEl.textContent = stepStateLabel(step.id, now);
}

function patchPipeline(now = performance.now()) {
  const root = document.getElementById("pipeline");
  if (!root) return;
  ensurePipelineMounted();
  for (const step of workflowState.steps) {
    const stepEl = root.querySelector(`[data-step="${step.id}"]`);
    if (stepEl) patchPipelineStep(stepEl, step, now);
  }
}

function ensureWorkflowNowStructure(el) {
  if (el.querySelector(".workflow-now-step")) return;
  el.innerHTML =
    '<strong class="workflow-now-step"></strong><span class="workflow-now-detail"></span><span class="workflow-now-elapsed"></span>';
}

function renderWorkflowNow() {
  const el = document.getElementById("workflow-now");
  if (!el) return;
  ensureWorkflowNowStructure(el);

  const stepEl = el.querySelector(".workflow-now-step");
  const detailEl = el.querySelector(".workflow-now-detail");
  const elapsedEl = el.querySelector(".workflow-now-elapsed");
  if (!stepEl || !detailEl || !elapsedEl) return;

  const now = performance.now();
  const runningStep = workflowState.steps.find(
    (step) => workflowState.stepStatus[step.id] === "running",
  );

  if (runningStep) {
    const detail = workflowState.stepDetail[runningStep.id] || "";
    const started = workflowState.stepStartedAt[runningStep.id];
    const elapsed = started ? formatElapsed(now - started) : "";
    el.dataset.idle = "false";
    delete el.dataset.tone;
    stepEl.textContent = runningStep.label;
    detailEl.textContent = detail ? ` · ${detail}` : "";
    elapsedEl.textContent = elapsed ? ` · ${elapsed}` : "";
    return;
  }

  const errorStep = [...workflowState.steps]
    .reverse()
    .find((step) => workflowState.stepStatus[step.id] === "error");
  if (errorStep) {
    const detail = workflowState.stepDetail[errorStep.id] || "Step failed";
    el.dataset.idle = "false";
    el.dataset.tone = "error";
    stepEl.textContent = errorStep.label;
    detailEl.textContent = ` · ${detail}`;
    elapsedEl.textContent = "";
    return;
  }

  el.dataset.idle = "true";
  delete el.dataset.tone;
  stepEl.textContent = "";
  detailEl.textContent = "";
  elapsedEl.textContent = "";
}

function patchWorkflowNowElapsed() {
  const el = document.getElementById("workflow-now");
  if (!el || el.dataset.idle === "true") return;

  const runningStep = workflowState.steps.find(
    (step) => workflowState.stepStatus[step.id] === "running",
  );
  if (!runningStep) return;

  const started = workflowState.stepStartedAt[runningStep.id];
  const elapsedEl = el.querySelector(".workflow-now-elapsed");
  if (elapsedEl) {
    elapsedEl.textContent = started
      ? ` · ${formatElapsed(performance.now() - started)}`
      : "";
  }

  const root = document.getElementById("pipeline");
  const stepEl = root?.querySelector(`[data-step="${runningStep.id}"]`);
  const stateEl = stepEl?.querySelector(".step-state");
  if (stateEl && started) {
    stateEl.textContent = formatElapsed(performance.now() - started);
  }
}

function queueStatusLabel(item) {
  const status = item.status || "queued";
  if (status === "running") return item.stepLabel || "Running";
  if (status === "done") return "Done";
  if (status === "review") return "Needs review";
  if (status === "cancelled") return "Cancelled";
  if (status === "error") return "Failed";
  return "Queued";
}

function updateQueueRowActions(li, item) {
  let actions = li.querySelector(".queue-actions");
  if (!actions) {
    actions = document.createElement("span");
    actions.className = "queue-actions";
    li.appendChild(actions);
  }
  actions.replaceChildren();
  if (item.status === "running") {
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn ghost compact queue-cancel";
    cancelBtn.textContent = "Cancel";
    cancelBtn.dataset.fileId = item.file_id || "";
    actions.appendChild(cancelBtn);
  } else if (item.status === "cancelled" || item.status === "error") {
    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn ghost compact queue-retry";
    retryBtn.textContent = "Retry";
    retryBtn.dataset.path = item.path || "";
    actions.appendChild(retryBtn);
  }
}

function mountQueue() {
  const root = document.getElementById("job-queue");
  if (!root) return;

  const key = queueFilesKey();
  workflowState.queueMountKey = key;
  root.replaceChildren();

  if (!workflowState.queue.length) {
    const empty = document.createElement("li");
    empty.className = "queue-empty";
    empty.textContent = "No active job";
    root.appendChild(empty);
    return;
  }

  for (const item of workflowState.queue) {
    const li = document.createElement("li");
    li.dataset.fileId = item.file_id || "";
    li.dataset.status = item.status || "queued";

    const nameEl = document.createElement("span");
    nameEl.className = "queue-name";
    nameEl.textContent = item.filename || "document";

    const statusEl = document.createElement("span");
    statusEl.className = "queue-status";
    statusEl.textContent = queueStatusLabel(item);

    li.append(nameEl, statusEl);
    updateQueueRowActions(li, item);
    root.appendChild(li);
  }
}

function patchQueueRow(fileId) {
  if (!fileId) return;
  const root = document.getElementById("job-queue");
  const item = workflowState.queue.find((q) => q.file_id === fileId);
  if (!root || !item) return;

  const li = root.querySelector(`[data-file-id="${fileId}"]`);
  if (!li) return;

  li.dataset.status = item.status || "queued";
  const statusEl = li.querySelector(".queue-status");
  if (statusEl) statusEl.textContent = queueStatusLabel(item);
  updateQueueRowActions(li, item);
}

function patchQueue() {
  const root = document.getElementById("job-queue");
  if (!root) return;

  const key = queueFilesKey();
  if (key !== workflowState.queueMountKey) {
    mountQueue();
    return;
  }

  if (!workflowState.queue.length) return;

  for (const item of workflowState.queue) {
    const li = root.querySelector(`[data-file-id="${item.file_id || ""}"]`);
    if (!li) continue;
    li.dataset.status = item.status || "queued";
    const nameEl = li.querySelector(".queue-name");
    const statusEl = li.querySelector(".queue-status");
    if (nameEl) nameEl.textContent = item.filename || "document";
    if (statusEl) statusEl.textContent = queueStatusLabel(item);
    updateQueueRowActions(li, item);
  }
}

function clearStepTimers() {
  for (const timer of Object.values(workflowState.stepTimers)) {
    window.clearTimeout(timer);
  }
  workflowState.stepTimers = {};
  workflowState.stepAnimQueue = [];
  workflowState.stepAnimRunning = false;
  workflowState.stepShownAt = {};
  stopElapsedTicker();
}

function resetStepStatuses(mode = "idle") {
  clearStepTimers();
  workflowState.stepStatus = {};
  workflowState.stepDetail = {};
  workflowState.stepStartedAt = {};
  for (const step of workflowState.steps) {
    workflowState.stepStatus[step.id] = mode === "queued" ? "wait" : "idle";
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return `${minutes}m ${rem.toString().padStart(2, "0")}s`;
}

function stopElapsedTicker() {
  if (workflowState.elapsedTimer) {
    window.clearInterval(workflowState.elapsedTimer);
    workflowState.elapsedTimer = null;
  }
}

function ensureElapsedTicker() {
  const hasRunning = Object.values(workflowState.stepStatus).includes("running");
  if (hasRunning && !workflowState.elapsedTimer) {
    workflowState.elapsedTimer = window.setInterval(() => {
      patchWorkflowNowElapsed();
    }, 1000);
  } else if (!hasRunning) {
    stopElapsedTicker();
  }
}

function applyStepVisual(stepId, status, detail, label) {
  workflowState.stepStatus[stepId] = status;
  if (detail != null && detail !== "") {
    workflowState.stepDetail[stepId] = detail;
  }
  if (status === "running") {
    if (!workflowState.stepStartedAt[stepId]) {
      workflowState.stepStartedAt[stepId] = performance.now();
    }
    workflowState.stepShownAt[stepId] = performance.now();
    const row = workflowState.queue.find(
      (q) => q.file_id === workflowState.activeFileId,
    );
    if (row) {
      row.status = "running";
      row.stepLabel = label || stepId;
    }
  } else {
    delete workflowState.stepStartedAt[stepId];
  }
  ensureElapsedTicker();
  renderWorkflowNow();
  patchPipeline();
  patchQueueRow(workflowState.activeFileId);
  updateWorkflowChrome();
}

async function drainStepAnimQueue() {
  if (workflowState.stepAnimRunning) return;
  workflowState.stepAnimRunning = true;
  while (workflowState.stepAnimQueue.length) {
    const item = workflowState.stepAnimQueue.shift();
    if (!item) break;
    // Drop stale updates from a previous file.
    if (
      item.fileId &&
      workflowState.activeFileId &&
      item.fileId !== workflowState.activeFileId
    ) {
      continue;
    }

    const { stepId, status, detail, label } = item;
    const current = workflowState.stepStatus[stepId];

    if (status === "running") {
      applyStepVisual(stepId, "running", detail, label);
      await sleep(STEP_MIN_VISIBLE_MS);
      continue;
    }

    // Terminal / skipped: keep running step visible briefly when switching steps.
    if (current === "running") {
      const shownAt = workflowState.stepShownAt[stepId] || 0;
      const elapsed = performance.now() - shownAt;
      if (elapsed < STEP_MIN_VISIBLE_MS) {
        await sleep(STEP_MIN_VISIBLE_MS - elapsed);
      }
      applyStepVisual(stepId, status, detail, label);
    } else {
      applyStepVisual(stepId, status, detail, label);
    }
    await sleep(80);
  }
  workflowState.stepAnimRunning = false;
}

function enqueueStepUpdate(stepId, status, detail, label, fileId) {
  if (!stepId) return;

  if (
    fileId &&
    workflowState.activeFileId &&
    fileId !== workflowState.activeFileId
  ) {
    return;
  }

  const normalizedStatus = status || "wait";
  const current = workflowState.stepStatus[stepId];

  // Detail-only running refresh — patch immediately, skip animation queue.
  if (normalizedStatus === "running" && current === "running") {
    if (detail != null && detail !== "") {
      workflowState.stepDetail[stepId] = detail;
    }
    const row = workflowState.queue.find(
      (q) => q.file_id === workflowState.activeFileId,
    );
    if (row) {
      row.stepLabel = label || stepId;
    }
    renderWorkflowNow();
    patchPipeline();
    patchQueueRow(workflowState.activeFileId);
    return;
  }

  workflowState.stepAnimQueue.push({
    stepId,
    status: normalizedStatus,
    detail,
    label,
    fileId,
  });
  drainStepAnimQueue();
}

function updateWorkflowChrome() {
  const panel = document.getElementById("workflow");
  const title = document.getElementById("workflow-title");
  const count = document.getElementById("workflow-count");
  if (!panel || !title || !count) return;

  const finishedStates = ["done", "error", "review"];
  const running = workflowState.queue.some((q) => q.status === "running");
  const hasJob = workflowState.queue.length > 0;
  panel.dataset.state = running ? "running" : hasJob ? "active" : "idle";

  if (workflowState.activeFilename) {
    title.textContent = `Processing ${workflowState.activeFilename}`;
  } else if (hasJob) {
    title.textContent = "Job ready";
  } else {
    title.textContent = "Waiting for files…";
  }

  if (workflowState.jobTotal > 0) {
    const done = workflowState.queue.filter((q) =>
      finishedStates.includes(q.status),
    ).length;
    const current = Math.min(done + (running ? 1 : 0), workflowState.jobTotal);
    count.textContent = `${current} / ${workflowState.jobTotal}`;
  } else {
    count.textContent = "";
  }
}

function renderWorkflow() {
  ensurePipelineMounted();
  renderWorkflowNow();
  patchPipeline();
  patchQueue();
  updateWorkflowChrome();
}

function setActiveFileSteps(filename) {
  workflowState.activeFilename = filename || null;
  resetStepStatuses("queued");
  renderWorkflow();
}

function handleWorkflowEvent(event) {
  const type = event.type;
  if (type === "hello" && Array.isArray(event.steps) && event.steps.length) {
    workflowState.steps = event.steps;
    resetStepStatuses("idle");
    renderWorkflow();
    return;
  }

  if (type === "job_started") {
    workflowState.jobTotal = event.total || (event.files || []).length || 0;
    workflowState.jobIndex = 0;
    workflowState.queue = (event.files || []).map((f) => ({
      file_id: f.file_id,
      filename: f.filename,
      path: f.path,
      status: "queued",
      stepLabel: null,
    }));
    workflowState.activeFileId = null;
    workflowState.activeFilename = null;
    setProcessInboxBusy(true);
    resetStepStatuses("idle");
    renderWorkflow();
    return;
  }

  if (type === "file_started") {
    workflowState.activeFileId = event.file_id;
    workflowState.activeFilename = event.filename;
    workflowState.jobIndex = (event.index || 0) + 1;
    workflowState.jobTotal = event.total || workflowState.jobTotal;
    const row = workflowState.queue.find((q) => q.file_id === event.file_id);
    if (row) {
      row.status = "running";
      row.stepLabel = "Starting";
    } else {
      workflowState.queue.push({
        file_id: event.file_id,
        filename: event.filename,
        path: event.path,
        status: "running",
        stepLabel: "Starting",
      });
    }
    setActiveFileSteps(event.filename);
    return;
  }

  if (type === "step") {
    if (event.file_id && workflowState.activeFileId && event.file_id !== workflowState.activeFileId) {
      // Ignore late events from a previous file.
      return;
    }
    if (event.filename) workflowState.activeFilename = event.filename;
    enqueueStepUpdate(
      event.step_id,
      event.status || "wait",
      event.detail,
      event.label || event.step_id,
      event.file_id || workflowState.activeFileId,
    );
    return;
  }

  if (type === "file_finished") {
    const row = workflowState.queue.find((q) => q.file_id === event.file_id);
    const finish = () => {
      if (row) {
        if (event.status === "error") {
          row.status = "error";
          row.stepLabel = "Failed";
        } else if (event.status === "review") {
          row.status = "review";
          row.stepLabel = "Needs review";
        } else if (event.status === "cancelled") {
          row.status = "cancelled";
          row.stepLabel = "Cancelled";
        } else {
          row.status = "done";
          row.stepLabel = "Done";
        }
      }
      if (event.file_id === workflowState.activeFileId) {
        if (event.status === "error") {
          for (const step of workflowState.steps) {
            if (workflowState.stepStatus[step.id] === "wait") {
              workflowState.stepStatus[step.id] = "idle";
            }
          }
        } else if (event.status === "cancelled") {
          for (const step of workflowState.steps) {
            const st = workflowState.stepStatus[step.id];
            if (st === "running" || st === "wait") {
              workflowState.stepStatus[step.id] = "idle";
            }
          }
          workflowState.activeFileId = null;
        } else {
          for (const step of workflowState.steps) {
            const st = workflowState.stepStatus[step.id];
            if (st === "running") workflowState.stepStatus[step.id] = "done";
          }
        }
      }
      renderWorkflow();
    };

    // Let queued step animations finish so fast pipelines still play through.
    const waitForAnim = async () => {
      while (workflowState.stepAnimQueue.length || workflowState.stepAnimRunning) {
        await sleep(40);
      }
      finish();
    };
    waitForAnim();
    return;
  }

  if (type === "job_finished") {
    setProcessInboxBusy(false);
    if (event.status === "empty") {
      workflowState.queue = [];
      workflowState.activeFileId = null;
      workflowState.activeFilename = null;
      workflowState.jobTotal = 0;
      resetStepStatuses("idle");
    }
    renderWorkflow();
    refreshInbox().catch(() => {});
    refreshDocs().catch(() => {});
    refreshReviews().catch(() => {});
    refreshHealth().catch(() => {});
  }
}

function connectWorkflowEvents() {
  if (!window.EventSource) return;
  const source = new EventSource("/api/process/events");
  source.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      handleWorkflowEvent(data);
    } catch (_err) {
      // ignore malformed frames
    }
  };
  source.onerror = () => {
    // Browser will reconnect automatically.
  };
}

/* ————— Settings: AI provider / auth ————— */

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

async function selectOllamaProvider({ enable = true, pullMissing = false } = {}) {
  setProviderUi("ollama");
  try {
    if (enable) {
      const data = await api("/api/ollama/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pull_missing: pullMissing }),
      });
      renderOllamaStatus(data.ollama);
      if (data.ollama?.ready) {
        toast("Using local Ollama", "ok");
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
  selectOllamaProvider({ enable: true, pullMissing: false });
});

document.getElementById("ollama-enable")?.addEventListener("click", () => {
  selectOllamaProvider({ enable: true, pullMissing: false });
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
    const enabled = await api("/api/ollama/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pull_missing: true }),
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

/* ————— Settings: filing & scanning ————— */

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
  return {
    source_dir,
    categories,
    batch: { poll_interval_seconds },
    review: { require_approval },
  };
}

function applySettingsToForm(settings) {
  document.getElementById("setup-source").value = settings.source_dir || "";
  renderCategories(settings.categories || []);
  knownCategories = (settings.categories || []).map((c) => c.name).filter(Boolean);
  const batch = settings.batch || {};
  const interval = batch.poll_interval_seconds ?? batch.delay_seconds ?? 30;
  document.getElementById("setup-poll-interval").value = interval;
  document.getElementById("setup-require-approval").checked =
    (settings.review || {}).require_approval ?? true;
  const catCount = (settings.categories || []).length;
  const scan =
    Number(interval) > 0 ? `scans every ${interval}s` : "manual scan only";
  setSetupStatus(
    `Source: ${settings.source_dir || "—"} · ${catCount} categor${catCount === 1 ? "y" : "ies"} · ${scan}`,
    "ok",
  );
}

async function refreshSetup() {
  const data = await api("/api/settings");
  applySettingsToForm(data.settings || {});
  return data.settings;
}

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

/* ————— Settings: appearance ————— */

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

document.getElementById("theme-grid").addEventListener("click", (e) => {
  const btn = e.target.closest(".theme-card");
  if (!btn) return;
  applyTheme(btn.dataset.themePreset);
});

function initTheme() {
  const param = new URLSearchParams(window.location.search).get("theme");
  let stored = null;
  try {
    stored = localStorage.getItem("pa-theme");
  } catch (_err) {
    // ignore
  }
  applyTheme(param || stored || "graphite");
}

/* ————— Settings: mockup mode ————— */

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

function applyMockScene() {
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
  renderAskResult(window.PA_MOCK.ask);
}

/* ————— Settings: software update ————— */

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

async function refreshUpdateVersion() {
  const data = await api("/api/update/status");
  setUpdateStatus(`PaperlessAgent v${data.current_version}`);
  const link = document.getElementById("update-repo-link");
  if (link && data.repo) link.href = `https://github.com/${data.repo}`;
}

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

/* ————— Settings: danger zone ————— */

document.getElementById("clear-all-data").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!armedConfirm(btn, "Really delete everything?")) return;
  btn.disabled = true;
  try {
    const data = await api("/api/data", { method: "DELETE" });
    setDangerStatus(data.message || "All stored data removed.", "ok");
    toast("All stored data removed", "ok");
    refreshDocs().catch(() => {});
    refreshInbox().catch(() => {});
  } catch (err) {
    const msg = String(err.message || err);
    setDangerStatus(msg, "error");
    toast(msg, "error");
  } finally {
    btn.disabled = false;
  }
});

/* ————— Init ————— */

renderRoute();
initTheme();
resetStepStatuses("idle");
renderWorkflow();
document.getElementById("mock-toggle").checked = Boolean(window.PA_MOCK?.enabled);
if (window.PA_MOCK?.enabled) {
  applyMockScene();
} else {
  connectWorkflowEvents();
}

refreshHealth()
  .then((health) => {
    if (health?.llm_provider === "ollama") return null;
    return refreshAuth();
  })
  .catch(() => {});
startHealthPolling();
refreshInbox().catch(() => {});
refreshDocs().catch(() => {});
refreshReviews().catch(() => {});
refreshUpdateVersion().catch(() => {});
refreshSetup()
  .catch((err) => {
    setSetupStatus(String(err.message || err), "err");
  })
  .finally(() => {
    // Re-render once categories are known so review cards get full select options.
    refreshReviews().catch(() => {});
  });
