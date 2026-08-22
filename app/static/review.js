import {
  api,
  armedConfirm,
  escapeHtml,
  isFinancialDocType,
  referenceIdsToString,
  toast,
} from "./api.js";
import { isTypingTarget } from "./keyboard.js";
import { knownCategories, hooks } from "./state.js";
import { fetchDocumentBlob, openDocumentFile } from "./ask.js";
import { mountPdfPreview } from "./pdf-preview.js";
import { currentView } from "./router.js";

export { isTypingTarget };

/** @typedef {{ filename: string|null, doc_type: string|null, doc_date: string|null, subject: string|null, counterparties: string|null, reference_ids: string[], summary: string|null, amount: number|null, currency: string|null }} ReviewOverrides */

const state = {
  reviews: /** @type {any[]} */ ([]),
  selectedId: /** @type {string|null} */ (null),
  baseline: /** @type {ReviewOverrides|null} */ (null),
  preview: /** @type {{ revoke: () => void } | null} */ (null),
  previewUnmount: /** @type {null | (() => void)} */ (null),
  previewCollapsed: false,
  loadToken: 0,
};

export function updateReviewBadge(count) {
  const badge = document.getElementById("review-badge");
  const nav = document.getElementById("nav-review");
  if (badge) {
    badge.textContent = String(count);
    badge.classList.toggle("hidden", !count);
    badge.setAttribute("aria-hidden", "true");
  }
  if (nav) {
    nav.setAttribute(
      "aria-label",
      count > 0 ? `Review, ${count} pending` : "Review",
    );
    nav.title = count > 0 ? `Review (${count} pending)` : "Review";
  }
}

export function nextIndexAfterRemoval(lengthBefore, removedIndex) {
  if (lengthBefore <= 1) return -1;
  if (removedIndex < lengthBefore - 1) return removedIndex;
  return removedIndex - 1;
}

export function adjacentIndex(length, currentIndex, delta) {
  if (length <= 0) return -1;
  const idx = currentIndex < 0 ? 0 : currentIndex;
  return (idx + delta + length) % length;
}

export function overridesEqual(a, b) {
  if (!a || !b) return a === b;
  const keys = [
    "filename",
    "doc_type",
    "doc_date",
    "subject",
    "counterparties",
    "summary",
    "amount",
    "currency",
  ];
  for (const key of keys) {
    if ((a[key] ?? null) !== (b[key] ?? null)) return false;
  }
  const aRefs = Array.isArray(a.reference_ids) ? a.reference_ids : [];
  const bRefs = Array.isArray(b.reference_ids) ? b.reference_ids : [];
  if (aRefs.length !== bRefs.length) return false;
  return aRefs.every((v, i) => v === bRefs[i]);
}

export function isDirty(baseline, current) {
  if (!baseline || !current) return false;
  return !overridesEqual(baseline, current);
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

function reviewFinancialFieldsHtml(p) {
  const show = isFinancialDocType(p.doc_type);
  return `<div class="review-fields-financial"${show ? "" : ' data-hidden="true"'}>
      <label class="field">
        <span>Amount</span>
        <input type="number" step="0.01" class="rv-amount" value="${escapeHtml(p.amount ?? "")}" placeholder="0.00" />
      </label>
      <label class="field">
        <span>Currency</span>
        <input type="text" class="rv-currency" value="${escapeHtml(p.currency || "")}" placeholder="EUR" maxlength="8" />
      </label>
    </div>`;
}

function syncReviewFinancialFields(root) {
  if (!root) return;
  const docType = root.querySelector(".rv-doc-type")?.value;
  const block = root.querySelector(".review-fields-financial");
  if (!block) return;
  const show = isFinancialDocType(docType);
  block.dataset.hidden = show ? "false" : "true";
  if (!show) {
    const amount = root.querySelector(".rv-amount");
    const currency = root.querySelector(".rv-currency");
    if (amount) amount.value = "";
    if (currency) currency.value = "";
  }
}

export function collectReviewOverrides(root) {
  const docType = root.querySelector(".rv-doc-type")?.value || null;
  const refRaw = root.querySelector(".rv-reference-ids")?.value.trim() || "";
  const overrides = {
    filename: root.querySelector(".rv-filename")?.value.trim() || null,
    doc_type: docType,
    doc_date: root.querySelector(".rv-doc-date")?.value.trim() || null,
    subject: root.querySelector(".rv-subject")?.value.trim() || null,
    counterparties: root.querySelector(".rv-counterparties")?.value.trim() || null,
    reference_ids: refRaw
      ? refRaw.split(/[,;]+/).map((part) => part.trim()).filter(Boolean)
      : [],
    summary: root.querySelector(".rv-summary")?.value.trim() || null,
    amount: null,
    currency: null,
  };
  if (isFinancialDocType(docType)) {
    const amountRaw = root.querySelector(".rv-amount")?.value.trim();
    overrides.currency = root.querySelector(".rv-currency")?.value.trim() || null;
    if (amountRaw && !Number.isNaN(Number(amountRaw))) {
      overrides.amount = Number(amountRaw);
    }
  }
  return overrides;
}

function proposalToBaseline(p) {
  const docType = p.doc_type || "other";
  const baseline = {
    filename: (p.filename || "").trim() || null,
    doc_type: docType,
    doc_date: (p.doc_date || "").trim() || null,
    subject: (p.subject || "").trim() || null,
    counterparties: (p.counterparties || "").trim() || null,
    reference_ids: Array.isArray(p.reference_ids)
      ? p.reference_ids.map((x) => String(x).trim()).filter(Boolean)
      : [],
    summary: (p.summary || "").trim() || null,
    amount: null,
    currency: null,
  };
  if (isFinancialDocType(docType)) {
    baseline.currency = (p.currency || "").trim() || null;
    if (typeof p.amount === "number" && !Number.isNaN(p.amount)) {
      baseline.amount = p.amount;
    } else if (p.amount != null && String(p.amount).trim() !== "") {
      const n = Number(p.amount);
      baseline.amount = Number.isNaN(n) ? null : n;
    }
  }
  return baseline;
}

function revokePreview() {
  if (state.previewUnmount) {
    state.previewUnmount();
    state.previewUnmount = null;
  }
  if (state.preview) {
    state.preview.revoke();
    state.preview = null;
  }
}

function announceStatus(message) {
  const live = document.getElementById("review-status");
  if (!live) return;
  live.textContent = "";
  // Force a change so polite live regions re-announce.
  window.requestAnimationFrame(() => {
    live.textContent = message;
  });
}

function selectedIndex() {
  return state.reviews.findIndex((r) => r.id === state.selectedId);
}

function selectedItem() {
  return state.reviews.find((r) => r.id === state.selectedId) || null;
}

function confirmLeaveIfDirty() {
  const editor = document.getElementById("review-editor");
  if (!editor || !state.baseline) return true;
  const current = collectReviewOverrides(editor);
  if (!isDirty(state.baseline, current)) return true;
  return window.confirm("You have unsaved edits. Discard them and switch documents?");
}

function updateDirtyIndicator() {
  const chip = document.getElementById("review-edited");
  const editor = document.getElementById("review-editor");
  if (!chip || !editor || !state.baseline) {
    if (chip) chip.hidden = true;
    return;
  }
  const dirty = isDirty(state.baseline, collectReviewOverrides(editor));
  chip.hidden = !dirty;
}

function queueItemHtml(item, selected) {
  const p = item.proposal || {};
  return `<button type="button" class="review-queue-item${selected ? " is-selected" : ""}"
      data-review-id="${escapeHtml(item.id)}"
      ${selected ? 'aria-current="true"' : ""}>
    <span class="doc-badge">${escapeHtml(p.doc_type || "other")}</span>
    <span class="review-queue-name">${escapeHtml(item.original_name || p.filename || "document")}</span>
  </button>`;
}

function editorHtml(item) {
  const p = item.proposal || {};
  return `${duplicateNoticeHtml(item.duplicates)}
    <div class="review-fields" id="review-editor">
      <div class="review-fields-grid">
      <label class="field full">
        <span>Filename</span>
        <input type="text" class="rv-filename" value="${escapeHtml(p.filename || "")}" />
      </label>
      <label class="field">
        <span>Category</span>
        <select class="rv-doc-type">${categoryOptionsHtml(p.doc_type || "other")}</select>
      </label>
      <label class="field">
        <span>Date</span>
        <input type="text" class="rv-doc-date" value="${escapeHtml(p.doc_date || "")}" placeholder="YYYY-MM-DD" />
      </label>
      <label class="field full">
        <span>Subject</span>
        <input type="text" class="rv-subject" value="${escapeHtml(p.subject || "")}" placeholder="What the document is about" />
      </label>
      <label class="field full">
        <span>People / organizations</span>
        <input type="text" class="rv-counterparties" value="${escapeHtml(p.counterparties || "")}" placeholder="Sender, doctor, insurer, employer…" />
      </label>
      <label class="field full">
        <span>Reference numbers</span>
        <input type="text" class="rv-reference-ids" value="${escapeHtml(referenceIdsToString(p.reference_ids))}" placeholder="Invoice #, policy #, case ref…" />
      </label>
      ${reviewFinancialFieldsHtml(p)}
      </div>
      <label class="field full field-summary">
        <span>Summary</span>
        <textarea class="rv-summary" rows="1">${escapeHtml(p.summary || "")}</textarea>
      </label>
    </div>
    <footer class="review-actions">
      <button type="button" class="btn primary review-approve" id="review-approve">
        <svg class="icon" aria-hidden="true"><use href="#i-check" /></svg>
        Approve &amp; file
      </button>
      <button type="button" class="btn ghost danger review-reject" id="review-reject">Reject &amp; remove scan</button>
    </footer>`;
}

function emptyStateHtml() {
  return `<div class="empty-state review-empty">
      <svg class="icon" aria-hidden="true"><use href="#i-review" /></svg>
      <p>Queue clear</p>
      <p class="fine">Nothing waiting for review. New scans will appear here when they need your approval.</p>
    </div>`;
}

function workbenchShellHtml() {
  return `<div class="review-workbench" id="review-workbench">
      <aside class="review-queue" aria-label="Review queue">
        <div class="review-queue-head">
          <span class="review-queue-title">Queue</span>
          <span class="review-queue-count" id="review-queue-count"></span>
        </div>
        <div class="review-queue-list" id="review-queue-list"></div>
      </aside>
      <div class="review-main">
        <section class="review-preview-pane${state.previewCollapsed ? " is-collapsed" : ""}" id="review-preview-pane">
          <header class="review-preview-toolbar">
            <button type="button" class="btn ghost compact" id="review-preview-toggle" aria-expanded="${state.previewCollapsed ? "false" : "true"}" aria-controls="review-preview-body">
              <svg class="icon" aria-hidden="true"><use href="#i-file" /></svg>
              <span class="review-preview-toggle-label">${state.previewCollapsed ? "Show preview" : "Hide preview"}</span>
            </button>
            <div class="review-preview-meta">
              <span class="review-preview-filename" id="review-preview-filename"></span>
              <span class="review-preview-count" id="review-preview-count"></span>
              <span class="review-edited" id="review-edited" hidden>Edited</span>
            </div>
            <div class="review-preview-actions">
              <button type="button" class="btn ghost compact" id="review-prev" title="Previous (k)">Previous</button>
              <button type="button" class="btn ghost compact" id="review-next" title="Next (j)">Next</button>
              <button type="button" class="btn ghost compact" id="review-open" title="Open in new window (o)">
                <svg class="icon" aria-hidden="true"><use href="#i-external" /></svg>
                Open in new window
              </button>
            </div>
          </header>
          <div class="review-preview-body" id="review-preview-body">
            <div class="review-preview-frame" id="review-preview-frame" role="img" aria-label="Document preview"></div>
          </div>
        </section>
        <section class="review-editor-pane" aria-label="Document metadata">
          <div id="review-editor-host"></div>
        </section>
      </div>
    </div>
    <div id="review-status" class="sr-only" aria-live="polite"></div>`;
}

async function loadPreview(item) {
  const frame = document.getElementById("review-preview-frame");
  if (!frame || !item) return;
  const token = ++state.loadToken;
  revokePreview();
  frame.innerHTML = `<div class="review-preview-loading">Loading preview…</div>`;

  const name = item.original_name || item.proposal?.filename || "document";
  frame.setAttribute("aria-label", `Preview of ${name}`);

  if (window.PA_MOCK?.enabled) {
    const p = item.proposal || {};
    frame.innerHTML = `<div class="review-preview-placeholder">
        <span class="doc-badge">${escapeHtml(p.doc_type || "other")}</span>
        <p class="review-preview-placeholder-title">${escapeHtml(name)}</p>
        <p class="fine">Mockup mode — preview is demo chrome only</p>
      </div>`;
    return;
  }

  try {
    const blob = await fetchDocumentBlob(
      `/api/reviews/${encodeURIComponent(item.id)}/file`,
    );
    if (token !== state.loadToken) {
      blob.revoke();
      return;
    }
    state.preview = blob;
    if (blob.isPdf || (blob.mime || "").includes("pdf")) {
      state.previewUnmount = mountPdfPreview(frame, blob.bytes);
    } else if ((blob.mime || "").startsWith("image/")) {
      frame.innerHTML = `<img class="review-preview-image" alt="Scan preview of ${escapeHtml(name)}" src="${blob.objectUrl}" />`;
    } else {
      frame.innerHTML = `<div class="review-preview-placeholder">
          <p>Preview not available for this file type</p>
          <p class="fine">${escapeHtml(blob.mime || "unknown")}</p>
        </div>`;
    }
  } catch (err) {
    if (token !== state.loadToken) return;
    frame.innerHTML = `<div class="review-preview-placeholder review-preview-error">
        <p>Could not load preview</p>
        <p class="fine">${escapeHtml(String(err.message || err))}</p>
      </div>`;
  }
}

function renderQueueChrome() {
  const item = selectedItem();
  const idx = selectedIndex();
  const total = state.reviews.length;
  const list = document.getElementById("review-queue-list");
  const countEl = document.getElementById("review-queue-count");
  const filenameEl = document.getElementById("review-preview-filename");
  const countLabel = document.getElementById("review-preview-count");
  const pane = document.getElementById("review-preview-pane");
  const toggle = document.getElementById("review-preview-toggle");

  if (countEl) countEl.textContent = String(total);
  if (list) {
    list.innerHTML = state.reviews
      .map((r) => queueItemHtml(r, r.id === state.selectedId))
      .join("");
  }
  if (filenameEl) {
    filenameEl.textContent = item
      ? item.original_name || item.proposal?.filename || "document"
      : "";
  }
  if (countLabel) {
    countLabel.textContent = item && idx >= 0 ? `${idx + 1} of ${total}` : "";
  }
  if (pane) pane.classList.toggle("is-collapsed", state.previewCollapsed);
  if (toggle) {
    toggle.setAttribute("aria-expanded", state.previewCollapsed ? "false" : "true");
    const label = toggle.querySelector(".review-preview-toggle-label");
    if (label) label.textContent = state.previewCollapsed ? "Show preview" : "Hide preview";
  }

  const prevBtn = document.getElementById("review-prev");
  const nextBtn = document.getElementById("review-next");
  const openBtn = document.getElementById("review-open");
  const disabled = total <= 0;
  if (prevBtn) prevBtn.disabled = total <= 1;
  if (nextBtn) nextBtn.disabled = total <= 1;
  if (openBtn) openBtn.disabled = disabled || Boolean(window.PA_MOCK?.enabled);
}

function renderEditorHost() {
  const host = document.getElementById("review-editor-host");
  const item = selectedItem();
  if (!host) return;
  if (!item) {
    host.innerHTML = emptyStateHtml();
    state.baseline = null;
    return;
  }
  host.innerHTML = editorHtml(item);
  state.baseline = proposalToBaseline(item.proposal || {});
  syncReviewFinancialFields(host);
  updateDirtyIndicator();
}

function renderWorkbenchChrome() {
  renderQueueChrome();
  renderEditorHost();
}

/** Keep the open editor when the queue grows around the same selected review. */
export function shouldPreserveReviewEditor(prevSelectedId, nextSelectedId, hasWorkbench) {
  return Boolean(hasWorkbench && prevSelectedId && prevSelectedId === nextSelectedId);
}

function renderEmptyRoot(root) {
  revokePreview();
  state.selectedId = null;
  state.baseline = null;
  root.innerHTML = emptyStateHtml();
}

function renderReviews(payload, options = {}) {
  const root = document.getElementById("reviews");
  if (!root) return;
  const reviews = payload.reviews || [];
  const preferId = options.preferId || null;
  const prevSelectedId = state.selectedId;
  const hadWorkbench = Boolean(document.getElementById("review-workbench"));
  state.reviews = reviews;
  updateReviewBadge(reviews.length);

  if (!reviews.length) {
    renderEmptyRoot(root);
    return;
  }

  if (preferId && reviews.some((r) => r.id === preferId)) {
    state.selectedId = preferId;
  } else if (!state.selectedId || !reviews.some((r) => r.id === state.selectedId)) {
    state.selectedId = reviews[0].id;
  }

  if (!hadWorkbench) {
    root.innerHTML = workbenchShellHtml();
    renderWorkbenchChrome();
    const item = selectedItem();
    if (item) loadPreview(item);
    return;
  }

  if (shouldPreserveReviewEditor(prevSelectedId, state.selectedId, true)) {
    renderQueueChrome();
    return;
  }

  renderWorkbenchChrome();
  const item = selectedItem();
  if (item) loadPreview(item);
}

export async function refreshReviews(options = {}) {
  const data = await api("/api/reviews");
  renderReviews(data, options);
  return data;
}

async function selectById(reviewId, { force = false } = {}) {
  if (!reviewId || reviewId === state.selectedId) return;
  if (!force && !confirmLeaveIfDirty()) return;
  state.selectedId = reviewId;
  renderWorkbenchChrome();
  const item = selectedItem();
  if (item) await loadPreview(item);
}

async function selectByDelta(delta) {
  const idx = selectedIndex();
  const next = adjacentIndex(state.reviews.length, idx, delta);
  if (next < 0) return;
  await selectById(state.reviews[next].id);
}

async function afterRemoveCurrent() {
  const removedIdx = selectedIndex();
  let preferId = null;
  if (removedIdx >= 0 && removedIdx < state.reviews.length - 1) {
    preferId = state.reviews[removedIdx + 1].id;
  } else if (removedIdx > 0) {
    preferId = state.reviews[removedIdx - 1].id;
  }
  state.selectedId = null;
  state.baseline = null;
  revokePreview();
  await refreshReviews({ preferId });
}

async function approveSelected() {
  const item = selectedItem();
  const editor = document.getElementById("review-editor");
  const btn = document.getElementById("review-approve");
  if (!item || !editor || !btn) return;
  btn.disabled = true;
  try {
    const data = await api(`/api/reviews/${encodeURIComponent(item.id)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectReviewOverrides(editor)),
    });
    const filed = data.filename || "document";
    toast(`Filed ${filed}`, "ok");
    announceStatus(`Filed ${filed}`);
    await afterRemoveCurrent();
    hooks.refreshDocs().catch(() => {});
    hooks.refreshInbox().catch(() => {});
  } catch (err) {
    toast(String(err.message || err), "error");
    btn.disabled = false;
  }
}

async function rejectSelected() {
  const item = selectedItem();
  const btn = document.getElementById("review-reject");
  if (!item || !btn) return;
  if (!armedConfirm(btn, "Really reject?")) return;
  btn.disabled = true;
  try {
    await api(`/api/reviews/${encodeURIComponent(item.id)}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete_file: true }),
    });
    toast("Rejected — scan removed from inbox", "ok");
    announceStatus("Rejected — scan removed from inbox");
    await afterRemoveCurrent();
    hooks.refreshInbox().catch(() => {});
  } catch (err) {
    toast(String(err.message || err), "error");
    btn.disabled = false;
  }
}

async function openSelected() {
  const item = selectedItem();
  if (!item) return;
  const btn = document.getElementById("review-open");
  if (btn) btn.disabled = true;
  try {
    await openDocumentFile(`/api/reviews/${encodeURIComponent(item.id)}/file`);
  } catch (err) {
    toast(String(err.message || err), "error");
  } finally {
    if (btn) btn.disabled = Boolean(window.PA_MOCK?.enabled) || state.reviews.length <= 0;
  }
}

export function initReview() {
  const root = document.getElementById("reviews");
  if (!root) return;

  root.addEventListener("change", (e) => {
    if (e.target.matches(".rv-doc-type")) {
      syncReviewFinancialFields(document.getElementById("review-editor-host"));
      updateDirtyIndicator();
    }
  });

  root.addEventListener("input", (e) => {
    if (e.target.closest("#review-editor")) updateDirtyIndicator();
  });

  root.addEventListener("click", async (e) => {
    const queueBtn = e.target.closest(".review-queue-item");
    if (queueBtn) {
      await selectById(queueBtn.dataset.reviewId);
      return;
    }
    if (e.target.closest("#review-prev")) {
      await selectByDelta(-1);
      return;
    }
    if (e.target.closest("#review-next")) {
      await selectByDelta(1);
      return;
    }
    if (e.target.closest("#review-preview-toggle")) {
      state.previewCollapsed = !state.previewCollapsed;
      renderWorkbenchChrome();
      return;
    }
    if (e.target.closest("#review-open")) {
      await openSelected();
      return;
    }
    if (e.target.closest("#review-approve") || e.target.closest(".review-approve")) {
      await approveSelected();
      return;
    }
    if (e.target.closest("#review-reject") || e.target.closest(".review-reject")) {
      await rejectSelected();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (currentView() !== "review") return;
    if (!state.reviews.length) return;

    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      approveSelected();
      return;
    }
    if (isTypingTarget(e.target)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (key === "j") {
      e.preventDefault();
      selectByDelta(1);
    } else if (key === "k") {
      e.preventDefault();
      selectByDelta(-1);
    } else if (key === "o") {
      e.preventDefault();
      openSelected();
    }
  });

  document.getElementById("refresh-reviews")?.addEventListener("click", () => {
    refreshReviews().catch((err) => toast(String(err.message || err), "error"));
  });
}
