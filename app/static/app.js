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
        <span>Counterparties</span>
        <input type="text" class="rv-counterparties" value="${escapeHtml(p.counterparties || "")}" />
      </label>
      <label class="field narrow">
        <span>Amount</span>
        <input type="number" step="0.01" class="rv-amount" value="${escapeHtml(p.amount ?? "")}" />
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
      const meta = [d.doc_date || "undated", d.counterparties || "", d.id]
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
    btn.disabled = false;
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
  } catch (err) {
    root.innerHTML = `<div class="ask-reply">${escapeHtml(String(err.message || err))}</div>`;
  }
});

/* ————— Workflow (SSE) ————— */

const DEFAULT_PIPELINE_STEPS = [
  { id: "read", label: "Read" },
  { id: "ai_ocr", label: "AI OCR" },
  { id: "extract", label: "Extract" },
  { id: "name", label: "Name" },
  { id: "review", label: "Review" },
  { id: "file", label: "File" },
  { id: "index", label: "Index" },
];

/** Minimum time each step stays visibly "running" before done/skip/error. */
const STEP_MIN_VISIBLE_MS = 450;

const workflowState = {
  steps: DEFAULT_PIPELINE_STEPS,
  stepStatus: {},
  stepDetail: {},
  queue: [],
  activeFileId: null,
  activeFilename: null,
  jobTotal: 0,
  jobIndex: 0,
  stepAnimQueue: [],
  stepAnimRunning: false,
  stepShownAt: {},
  stepTimers: {},
};

function clearStepTimers() {
  for (const timer of Object.values(workflowState.stepTimers)) {
    window.clearTimeout(timer);
  }
  workflowState.stepTimers = {};
  workflowState.stepAnimQueue = [];
  workflowState.stepAnimRunning = false;
  workflowState.stepShownAt = {};
}

function resetStepStatuses(mode = "idle") {
  clearStepTimers();
  workflowState.stepStatus = {};
  workflowState.stepDetail = {};
  for (const step of workflowState.steps) {
    workflowState.stepStatus[step.id] = mode === "queued" ? "wait" : "idle";
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function applyStepVisual(stepId, status, detail, label) {
  workflowState.stepStatus[stepId] = status;
  if (detail) workflowState.stepDetail[stepId] = detail;
  if (status === "running") {
    workflowState.stepShownAt[stepId] = performance.now();
    const row = workflowState.queue.find(
      (q) => q.file_id === workflowState.activeFileId,
    );
    if (row) {
      row.status = "running";
      row.stepLabel = label || stepId;
    }
  }
  renderWorkflow();
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
      // Hold long enough to be readable; later terminal update may wait out the rest.
      await sleep(STEP_MIN_VISIBLE_MS);
      continue;
    }

    // Terminal / skipped: keep the step on screen long enough to register.
    if (current === "running") {
      const shownAt = workflowState.stepShownAt[stepId] || 0;
      const elapsed = performance.now() - shownAt;
      if (elapsed < STEP_MIN_VISIBLE_MS) {
        await sleep(STEP_MIN_VISIBLE_MS - elapsed);
      }
      applyStepVisual(stepId, status, detail, label);
    } else {
      // Never saw "running" (or it was instant) — flash active, then settle.
      applyStepVisual(stepId, "running", detail, label);
      await sleep(STEP_MIN_VISIBLE_MS);
      applyStepVisual(stepId, status, detail, label);
    }
    // Brief beat so the settled state is visible before the next step.
    await sleep(120);
  }
  workflowState.stepAnimRunning = false;
}

function enqueueStepUpdate(stepId, status, detail, label, fileId) {
  if (!stepId) return;
  workflowState.stepAnimQueue.push({
    stepId,
    status: status || "wait",
    detail,
    label,
    fileId,
  });
  drainStepAnimQueue();
}

function renderPipeline() {
  const root = document.getElementById("pipeline");
  if (!root) return;
  root.innerHTML = workflowState.steps
    .map((step, index) => {
      const status = workflowState.stepStatus[step.id] || "idle";
      const detail = workflowState.stepDetail[step.id] || "";
      const stateLabel =
        status === "running"
          ? "run"
          : status === "done"
            ? "done"
            : status === "error"
              ? "err"
              : status === "skipped"
                ? "skip"
                : "wait";
      const connector =
        index < workflowState.steps.length - 1
          ? '<span class="pipeline-connector" aria-hidden="true"></span>'
          : "";
      return `<li class="pipeline-step" data-status="${escapeHtml(status)}" data-step="${escapeHtml(step.id)}" title="${escapeHtml(detail)}">
        <div class="pipeline-node">
          <span class="step-label">${escapeHtml(step.label)}</span>
          <span class="step-state">${escapeHtml(stateLabel)}</span>
        </div>
        ${connector}
      </li>`;
    })
    .join("");
}

function renderQueue() {
  const root = document.getElementById("job-queue");
  if (!root) return;
  if (!workflowState.queue.length) {
    root.innerHTML = '<li class="queue-empty">No active job</li>';
    return;
  }
  root.innerHTML = workflowState.queue
    .map((item) => {
      const status = item.status || "queued";
      const label =
        status === "running"
          ? item.stepLabel || "Running"
          : status === "done"
            ? "Done"
            : status === "review"
              ? "Needs review"
              : status === "error"
                ? "Failed"
                : "Queued";
      return `<li data-status="${escapeHtml(status)}" data-file-id="${escapeHtml(item.file_id || "")}">
        <span class="queue-name">${escapeHtml(item.filename || "document")}</span>
        <span class="queue-status">${escapeHtml(label)}</span>
      </li>`;
    })
    .join("");
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
  renderPipeline();
  renderQueue();
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
  workflowState.jobTotal = scene.jobTotal;
  workflowState.stepStatus = { ...scene.stepStatus };
  workflowState.stepDetail = { ...scene.stepDetail };
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
