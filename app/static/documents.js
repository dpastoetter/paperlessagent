import {
  api,
  escapeHtml,
  isFinancialDocType,
  toast,
} from "./api.js";
import { knownCategories } from "./state.js";
import { fetchDocumentBlob, openDocumentFile } from "./ask.js";
import { mountPdfPreview } from "./pdf-preview.js";
import { currentView, parseHashQuery, setHashQuery } from "./router.js";
import { registerArchiveDrawerOverlay } from "./keyboard.js";

export const ARCHIVE_PAGE_SIZE = 40;
export const ARCHIVE_DEBOUNCE_MS = 300;
export const ARCHIVE_MIN_QUERY_LEN = 2;

const FILTER_KEYS = ["q", "doc_type", "counterparty", "date_from", "date_to"];

export function archiveShellHtml() {
  return `<form id="search-form" class="archive-toolbar search" role="search">
          <div class="archive-toolbar-top">
            <label class="field grow">
              <span>Search</span>
              <input type="search" id="search-q" placeholder="Acme, invoice, tax…" autocomplete="off" />
            </label>
            <button type="button" class="btn ghost compact" id="archive-filters-toggle" aria-expanded="false" aria-controls="archive-filters-panel">
              <span class="archive-filters-toggle-label">Filters</span>
              <span class="archive-filter-count" id="archive-filter-count" hidden></span>
            </button>
            <button type="submit" class="btn secondary">
              <svg class="icon" aria-hidden="true"><use href="#i-search" /></svg>
              Search
            </button>
            <button type="button" class="btn ghost" id="search-clear" hidden>Clear filters</button>
          </div>
          <div class="archive-filters-panel" id="archive-filters-panel">
            <label class="field narrow">
              <span>Category</span>
              <select id="search-type">
                <option value="">Any</option>
              </select>
            </label>
            <label class="field narrow">
              <span>People / organization</span>
              <input type="text" id="search-counterparty" placeholder="Acme, Dr. Weber…" autocomplete="off" />
            </label>
            <label class="field narrow">
              <span>Date from</span>
              <input type="date" id="search-date-from" />
            </label>
            <label class="field narrow">
              <span>Date to</span>
              <input type="date" id="search-date-to" />
            </label>
          </div>
          <div class="archive-toolbar-status">
            <span id="archive-result-count" class="archive-result-count">—</span>
            <span id="archive-status" class="archive-status sr-only" aria-live="polite"></span>
          </div>
        </form>

        <div class="archive-workspace">
          <div class="archive-list-pane">
            <div id="docs" class="docs" aria-label="Archive results"></div>
            <div class="archive-list-footer" id="archive-list-footer" hidden>
              <button type="button" class="btn ghost" id="archive-load-more">Load more</button>
            </div>
          </div>
        </div>`;
}

export function archiveDrawerHtml() {
  return `<div id="archive-drawer-backdrop" class="archive-drawer-backdrop" hidden></div>
        <aside id="archive-drawer" class="archive-drawer" role="dialog" aria-modal="true" aria-labelledby="archive-drawer-title" hidden>
          <header class="archive-drawer-head">
            <button type="button" class="btn ghost compact" id="archive-drawer-close" aria-label="Close document details">Close</button>
          </header>
          <div id="archive-drawer-body" class="archive-drawer-body"></div>
        </aside>`;
}

export function mountArchiveShell() {
  const host = document.getElementById("archive");
  const view = document.getElementById("view-archive");
  if (host && !host.querySelector("#search-form")) {
    host.innerHTML = archiveShellHtml();
  }
  if (view && !document.getElementById("archive-drawer")) {
    view.insertAdjacentHTML("beforeend", archiveDrawerHtml());
  }
  return host;
}

const state = {
  filters: emptyFilters(),
  documents: /** @type {any[]} */ ([]),
  hasMore: false,
  offset: 0,
  loading: false,
  error: /** @type {string|null} */ (null),
  lastRequestKey: "",
  debounceTimer: /** @type {number|null} */ (null),
  selectedId: /** @type {string|null} */ (null),
  detail: /** @type {any|null} */ (null),
  detailLoading: false,
  detailError: /** @type {string|null} */ (null),
  preview: /** @type {{ revoke: () => void } | null} */ (null),
  previewUnmount: /** @type {null | (() => void)} */ (null),
  previewToken: 0,
  focusReturnId: /** @type {string|null} */ (null),
  filtersOpen: false,
  applyingHash: false,
  syncingHash: false,
};

export function emptyFilters() {
  return {
    q: "",
    doc_type: "",
    counterparty: "",
    date_from: "",
    date_to: "",
  };
}

export function normalizeFilters(raw = {}) {
  const out = emptyFilters();
  for (const key of FILTER_KEYS) {
    if (raw[key] != null) out[key] = String(raw[key]).trim();
  }
  return out;
}

export function filtersFromHashQuery(query = {}) {
  return normalizeFilters(query);
}

export function filtersToHashParams(filters, docId = null) {
  const params = {};
  for (const key of FILTER_KEYS) {
    if (filters[key]) params[key] = filters[key];
  }
  if (docId) params.doc = docId;
  return params;
}

export function countActiveFilters(filters) {
  return FILTER_KEYS.reduce((n, key) => n + (filters[key] ? 1 : 0), 0);
}

export function filtersEqual(a, b) {
  return FILTER_KEYS.every((key) => (a?.[key] || "") === (b?.[key] || ""));
}

export function shouldAutoSearchQuery(q) {
  const text = String(q || "").trim();
  return text.length === 0 || text.length >= ARCHIVE_MIN_QUERY_LEN;
}

export function buildDocumentsQuery(filters, { limit = ARCHIVE_PAGE_SIZE, offset = 0 } = {}) {
  const params = { limit: String(limit), offset: String(offset) };
  for (const key of FILTER_KEYS) {
    if (filters[key]) params[key] = filters[key];
  }
  return params;
}

export function requestKey(params) {
  return new URLSearchParams(params).toString();
}

export function archiveListMessage({ documents, filters, error, loading }) {
  if (error) return { kind: "error", text: error };
  if (loading && !(documents || []).length) return { kind: "loading", text: "Searching archive…" };
  if ((documents || []).length) return { kind: "results", text: null };
  if (countActiveFilters(filters)) {
    return { kind: "no-results", text: "No documents match these filters." };
  }
  return { kind: "empty", text: "No documents yet" };
}

export function docsEmptyState(message) {
  return `<div class="empty-state">
    <svg class="icon" aria-hidden="true"><use href="#i-archive" /></svg>
    <p>${escapeHtml(message)}</p>
  </div>`;
}

function formatAmount(amount, currency) {
  if (typeof amount !== "number" || Number.isNaN(amount)) return "";
  const cur = currency || "";
  return cur ? `${amount} ${cur}` : String(amount);
}

function categoryOptionsHtml(selected) {
  const names = knownCategories.length ? knownCategories : [];
  const options = ['<option value="">Any</option>'];
  for (const name of names) {
    options.push(
      `<option value="${escapeHtml(name)}"${name === selected ? " selected" : ""}>${escapeHtml(name)}</option>`,
    );
  }
  if (selected && !names.includes(selected)) {
    options.push(
      `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`,
    );
  }
  return options.join("");
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

function syncHash({ replace = true } = {}) {
  if (state.applyingHash || state.syncingHash) return;
  state.syncingHash = true;
  try {
    setHashQuery("archive", filtersToHashParams(state.filters, state.selectedId), { replace });
  } finally {
    state.syncingHash = false;
  }
}

function readToolbarFilters() {
  return normalizeFilters({
    q: document.getElementById("search-q")?.value,
    doc_type: document.getElementById("search-type")?.value,
    counterparty: document.getElementById("search-counterparty")?.value,
    date_from: document.getElementById("search-date-from")?.value,
    date_to: document.getElementById("search-date-to")?.value,
  });
}

function writeToolbarFilters(filters) {
  const q = document.getElementById("search-q");
  const type = document.getElementById("search-type");
  const party = document.getElementById("search-counterparty");
  const from = document.getElementById("search-date-from");
  const to = document.getElementById("search-date-to");
  if (q) q.value = filters.q || "";
  if (type) {
    type.innerHTML = categoryOptionsHtml(filters.doc_type || "");
    type.value = filters.doc_type || "";
  }
  if (party) party.value = filters.counterparty || "";
  if (from) from.value = filters.date_from || "";
  if (to) to.value = filters.date_to || "";
  updateFilterChrome();
}

function updateFilterChrome() {
  const active = countActiveFilters(state.filters);
  const clearBtn = document.getElementById("search-clear");
  const countEl = document.getElementById("archive-filter-count");
  const toggle = document.getElementById("archive-filters-toggle");
  const panel = document.getElementById("archive-filters-panel");
  if (clearBtn) {
    clearBtn.hidden = active === 0;
    clearBtn.disabled = active === 0;
  }
  if (countEl) {
    countEl.textContent = active ? String(active) : "";
    countEl.hidden = active === 0;
  }
  if (toggle) {
    toggle.setAttribute("aria-expanded", state.filtersOpen ? "true" : "false");
    const label = toggle.querySelector(".archive-filters-toggle-label");
    if (label) label.textContent = state.filtersOpen ? "Hide filters" : "Filters";
  }
  if (panel) panel.classList.toggle("is-open", state.filtersOpen);
}

function setStatus(text, { busy = false } = {}) {
  const status = document.getElementById("archive-status");
  if (!status) return;
  status.textContent = text || "";
  status.dataset.busy = busy ? "true" : "false";
}

function rowHtml(d, selected) {
  const title = d.subject || d.filename || d.original_name || d.id;
  const summary = d.summary || "";
  const badge = d.doc_type || "other";
  const party = d.counterparties || "";
  const amount = isFinancialDocType(d.doc_type) ? formatAmount(d.amount, d.currency) : "";
  const subtle = d.filename || d.original_name || "";
  return `<button type="button" class="doc-row${selected ? " is-selected" : ""}"
      id="doc-row-${escapeHtml(d.id)}"
      data-doc-id="${escapeHtml(d.id)}"
      ${selected ? 'aria-current="true"' : ""}>
    <span class="doc-row-main">
      <span class="doc-title-row">
        <span class="doc-badge">${escapeHtml(badge)}</span>
        <span class="doc-row-title">${escapeHtml(title)}</span>
      </span>
      ${summary ? `<span class="doc-row-summary">${escapeHtml(summary)}</span>` : ""}
      <span class="doc-row-meta">
        <span>${escapeHtml(d.doc_date || "undated")}</span>
        ${party ? `<span>${escapeHtml(party)}</span>` : ""}
        ${amount ? `<span class="doc-row-amount">${escapeHtml(amount)}</span>` : ""}
        ${subtle ? `<span class="doc-row-file">${escapeHtml(subtle)}</span>` : ""}
      </span>
    </span>
  </button>`;
}

function renderList() {
  const root = document.getElementById("docs");
  const footer = document.getElementById("archive-list-footer");
  if (!root) return;

  const message = archiveListMessage({
    documents: state.documents,
    filters: state.filters,
    error: state.error,
    loading: state.loading,
  });

  const resultLabel = document.getElementById("archive-result-count");
  if (resultLabel) {
    if (state.loading && !state.documents.length) {
      resultLabel.textContent = "Loading…";
    } else if (state.documents.length) {
      const more = state.hasMore ? "+" : "";
      resultLabel.textContent = `${state.documents.length}${more} result${state.documents.length === 1 ? "" : "s"}`;
    } else {
      resultLabel.textContent = message.kind === "empty" ? "0 documents" : "0 results";
    }
  }

  if (message.kind === "error" && !state.documents.length) {
    root.innerHTML = docsEmptyState(message.text);
  } else if (message.kind === "loading") {
    root.innerHTML = docsEmptyState(message.text);
  } else if (message.kind === "empty" || message.kind === "no-results") {
    root.innerHTML = docsEmptyState(message.text);
  } else {
    root.innerHTML = state.documents
      .map((d) => rowHtml(d, d.id === state.selectedId))
      .join("");
  }

  if (state.error && state.documents.length) {
    let banner = document.getElementById("archive-error-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "archive-error-banner";
      banner.className = "archive-error-banner";
      banner.setAttribute("role", "alert");
      root.prepend(banner);
    }
    banner.textContent = state.error;
  } else {
    document.getElementById("archive-error-banner")?.remove();
  }

  if (footer) {
    footer.hidden = !state.hasMore;
    const moreBtn = document.getElementById("archive-load-more");
    if (moreBtn) moreBtn.disabled = state.loading;
  }

  root.setAttribute("aria-busy", state.loading ? "true" : "false");
  setStatus(
    state.loading ? "Loading…" : state.error || "",
    { busy: state.loading },
  );
}

async function loadPreview(doc) {
  const frame = document.getElementById("archive-preview-frame");
  if (!frame || !doc) return;
  const token = ++state.previewToken;
  revokePreview();
  const name = doc.filename || doc.original_name || "document";
  frame.setAttribute("aria-label", `Preview of ${name}`);

  if (window.PA_MOCK?.enabled) {
    frame.innerHTML = `<div class="archive-preview-placeholder">
        <span class="doc-badge">${escapeHtml(doc.doc_type || "other")}</span>
        <p class="archive-preview-placeholder-title">${escapeHtml(name)}</p>
        <p class="fine">Mockup mode — preview is demo chrome only</p>
      </div>`;
    return;
  }

  frame.innerHTML = `<div class="archive-preview-loading">Loading preview…</div>`;
  try {
    const blob = await fetchDocumentBlob(
      `/api/documents/${encodeURIComponent(doc.id)}/file`,
    );
    if (token !== state.previewToken) {
      blob.revoke();
      return;
    }
    state.preview = blob;
    if (blob.isPdf || (blob.mime || "").includes("pdf")) {
      state.previewUnmount = mountPdfPreview(frame, blob.bytes);
    } else if ((blob.mime || "").startsWith("image/")) {
      frame.innerHTML = `<img class="archive-preview-image" alt="Preview of ${escapeHtml(name)}" src="${blob.objectUrl}" />`;
    } else {
      frame.innerHTML = `<div class="archive-preview-placeholder">
          <p>Preview not available for this file type</p>
          <p class="fine">${escapeHtml(blob.mime || "unknown")}</p>
        </div>`;
    }
  } catch (err) {
    if (token !== state.previewToken) return;
    frame.innerHTML = `<div class="archive-preview-placeholder archive-preview-error">
        <p>Could not load preview</p>
        <p class="fine">${escapeHtml(String(err.message || err))}</p>
      </div>`;
  }
}

function renderDrawer() {
  const drawer = document.getElementById("archive-drawer");
  const backdrop = document.getElementById("archive-drawer-backdrop");
  if (!drawer || !backdrop) return;

  const open = Boolean(state.selectedId);
  drawer.hidden = !open;
  backdrop.hidden = !open;
  drawer.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("archive-drawer-open", open);

  const body = document.getElementById("archive-drawer-body");
  if (!body) return;

  if (!open) {
    revokePreview();
    body.innerHTML = "";
    return;
  }

  if (state.detailLoading && !state.detail) {
    body.innerHTML = `<div class="archive-drawer-loading">Loading document…</div>`;
    return;
  }

  if (state.detailError && !state.detail) {
    body.innerHTML = `<div class="archive-drawer-error" role="alert">
        <p>Could not load document</p>
        <p class="fine">${escapeHtml(state.detailError)}</p>
      </div>`;
    return;
  }

  const d = state.detail || state.documents.find((x) => x.id === state.selectedId) || {};
  const title = d.subject || d.filename || d.original_name || d.id;
  const amount = isFinancialDocType(d.doc_type) ? formatAmount(d.amount, d.currency) : "";
  const path = d.path || "";

  body.innerHTML = `
    <div class="archive-drawer-preview" id="archive-preview-frame" role="img" aria-label="Document preview"></div>
    <div class="archive-drawer-meta">
      <div class="doc-title-row">
        <span class="doc-badge">${escapeHtml(d.doc_type || "other")}</span>
        <h2 id="archive-drawer-title">${escapeHtml(title)}</h2>
      </div>
      <dl class="archive-meta-grid">
        <div><dt>Date</dt><dd>${escapeHtml(d.doc_date || "—")}</dd></div>
        <div><dt>People / organizations</dt><dd>${escapeHtml(d.counterparties || "—")}</dd></div>
        ${amount ? `<div><dt>Amount</dt><dd>${escapeHtml(amount)}</dd></div>` : ""}
        <div class="full"><dt>Filename</dt><dd>${escapeHtml(d.filename || d.original_name || "—")}</dd></div>
        <div class="full"><dt>Summary</dt><dd>${escapeHtml(d.summary || "—")}</dd></div>
      </dl>
      <details class="archive-details">
        <summary>Details</summary>
        <dl class="archive-meta-grid compact">
          <div><dt>Document id</dt><dd class="mono">${escapeHtml(d.id || "—")}</dd></div>
          <div><dt>Original name</dt><dd>${escapeHtml(d.original_name || "—")}</dd></div>
          ${path ? `<div class="full"><dt>Archive path</dt><dd class="mono path">${escapeHtml(path)}</dd></div>` : ""}
          ${d.created_at ? `<div><dt>Created</dt><dd>${escapeHtml(d.created_at)}</dd></div>` : ""}
        </dl>
      </details>
      <div class="archive-drawer-actions">
        <button type="button" class="btn primary" id="archive-open-doc">
          <svg class="icon" aria-hidden="true"><use href="#i-external" /></svg>
          Open document
        </button>
        <button type="button" class="btn ghost" id="archive-reveal-doc">Reveal in folder</button>
      </div>
      ${state.detailError ? `<p class="archive-drawer-inline-error" role="alert">${escapeHtml(state.detailError)}</p>` : ""}
    </div>`;

  loadPreview(d);
  const openBtn = document.getElementById("archive-open-doc");
  if (window.PA_MOCK?.enabled && openBtn) openBtn.disabled = true;
}

async function openDocumentDetail(docId, { focus = true } = {}) {
  if (!docId) return;
  state.focusReturnId = docId;
  state.selectedId = docId;
  state.detailLoading = true;
  state.detailError = null;
  if (!state.detail || state.detail.id !== docId) {
    state.detail = state.documents.find((d) => d.id === docId) || null;
  }
  renderList();
  renderDrawer();
  syncHash();
  registerArchiveDrawerOverlay({
    open: true,
    onClose: () => closeDocumentDetail(),
  });

  if (focus) {
    window.requestAnimationFrame(() => {
      document.getElementById("archive-drawer-close")?.focus();
    });
  }

  try {
    const data = await api(`/api/documents/${encodeURIComponent(docId)}`);
    if (state.selectedId !== docId) return;
    state.detail = data.document || null;
    state.detailError = null;
  } catch (err) {
    if (state.selectedId !== docId) return;
    state.detailError = String(err.message || err);
  } finally {
    if (state.selectedId === docId) {
      state.detailLoading = false;
      renderDrawer();
    }
  }
}

export function closeDocumentDetail({ restoreFocus = true } = {}) {
  const returnId = state.focusReturnId || state.selectedId;
  state.selectedId = null;
  state.detail = null;
  state.detailError = null;
  state.detailLoading = false;
  revokePreview();
  renderList();
  renderDrawer();
  syncHash();
  registerArchiveDrawerOverlay({ open: false });
  if (restoreFocus && returnId) {
    window.requestAnimationFrame(() => {
      document.getElementById(`doc-row-${returnId}`)?.focus();
    });
  }
}

/** Navigate to Archive and open the document detail drawer for ``docId``. */
export function openArchiveDocument(docId) {
  if (!docId) return;
  setHashQuery("archive", filtersToHashParams(state.filters, docId), { replace: false });
}

async function fetchPage({ append = false } = {}) {
  const offset = append ? state.documents.length : 0;
  const params = buildDocumentsQuery(state.filters, {
    limit: ARCHIVE_PAGE_SIZE,
    offset,
  });
  const key = requestKey(params);
  if (!append && key === state.lastRequestKey && state.documents.length) {
    return;
  }
  if (!append) state.lastRequestKey = key;

  state.loading = true;
  state.error = null;
  if (!append) renderList();
  else {
    const moreBtn = document.getElementById("archive-load-more");
    if (moreBtn) moreBtn.disabled = true;
    setStatus("Loading more…", { busy: true });
  }

  try {
    const qs = new URLSearchParams(params);
    const data = await api(`/api/documents?${qs.toString()}`);
    const docs = data.documents || [];
    state.documents = append ? [...state.documents, ...docs] : docs;
    state.hasMore = Boolean(data.has_more);
    state.offset = Number(data.offset || offset) || 0;
    state.error = null;
  } catch (err) {
    state.error = String(err.message || err);
    if (!append) state.documents = [];
    state.hasMore = false;
  } finally {
    state.loading = false;
    renderList();
  }
}

export async function refreshDocs(params) {
  if (params && typeof params === "object" && Object.keys(params).length) {
    state.filters = normalizeFilters(params);
    writeToolbarFilters(state.filters);
  } else {
    writeToolbarFilters(state.filters);
  }
  state.lastRequestKey = "";
  await fetchPage({ append: false });
  const hash = parseHashQuery();
  if (hash.doc && currentView() === "archive") {
    await openDocumentDetail(hash.doc, { focus: false });
  }
}

function scheduleAutoSearch() {
  if (state.debounceTimer) window.clearTimeout(state.debounceTimer);
  state.debounceTimer = window.setTimeout(() => {
    state.debounceTimer = null;
    const next = readToolbarFilters();
    if (!shouldAutoSearchQuery(next.q)) return;
    if (filtersEqual(next, state.filters) && state.documents.length) return;
    state.filters = next;
    updateFilterChrome();
    syncHash();
    state.lastRequestKey = "";
    fetchPage({ append: false });
  }, ARCHIVE_DEBOUNCE_MS);
}

function applySearchNow({ fromHash = false } = {}) {
  if (state.debounceTimer) {
    window.clearTimeout(state.debounceTimer);
    state.debounceTimer = null;
  }
  const next = fromHash ? state.filters : readToolbarFilters();
  state.filters = next;
  writeToolbarFilters(next);
  updateFilterChrome();
  if (!fromHash) syncHash();
  state.lastRequestKey = "";
  return fetchPage({ append: false });
}

function clearFilters() {
  state.filters = emptyFilters();
  writeToolbarFilters(state.filters);
  syncHash();
  state.lastRequestKey = "";
  return fetchPage({ append: false });
}

function hydrateFromHash() {
  if (currentView() !== "archive") return;
  if (state.syncingHash) return;
  const query = parseHashQuery();
  const next = filtersFromHashQuery(query);
  const docId = query.doc || null;
  const filtersChanged = !filtersEqual(next, state.filters);
  state.applyingHash = true;
  state.filters = next;
  writeToolbarFilters(next);
  state.applyingHash = false;

  const run = async () => {
    if (filtersChanged || !state.documents.length) {
      state.lastRequestKey = "";
      await fetchPage({ append: false });
    }
    if (docId) {
      if (state.selectedId !== docId) await openDocumentDetail(docId, { focus: false });
    } else if (state.selectedId) {
      closeDocumentDetail({ restoreFocus: false });
    }
  };
  run().catch((err) => toast(String(err.message || err), "error"));
}

export function syncDocumentFilters() {
  writeToolbarFilters(state.filters);
}

export function initDocuments() {
  mountArchiveShell();
  writeToolbarFilters(state.filters);
  renderList();
  renderDrawer();

  const form = document.getElementById("search-form");
  form?.addEventListener("submit", (e) => {
    e.preventDefault();
    applySearchNow();
  });

  form?.addEventListener("input", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.id === "search-q" || target.id === "search-counterparty") {
      scheduleAutoSearch();
    }
  });

  form?.addEventListener("change", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (
      target.id === "search-type" ||
      target.id === "search-date-from" ||
      target.id === "search-date-to"
    ) {
      applySearchNow();
    }
  });

  document.getElementById("search-clear")?.addEventListener("click", () => {
    clearFilters();
  });

  document.getElementById("archive-filters-toggle")?.addEventListener("click", () => {
    state.filtersOpen = !state.filtersOpen;
    updateFilterChrome();
  });

  document.getElementById("archive-load-more")?.addEventListener("click", () => {
    fetchPage({ append: true });
  });

  document.getElementById("docs")?.addEventListener("click", (e) => {
    const row = e.target.closest(".doc-row");
    if (!row) return;
    openDocumentDetail(row.dataset.docId);
  });

  document.getElementById("archive-drawer-close")?.addEventListener("click", () => {
    closeDocumentDetail();
  });

  document.getElementById("archive-drawer-backdrop")?.addEventListener("click", () => {
    closeDocumentDetail();
  });

  document.getElementById("archive-drawer")?.addEventListener("click", async (e) => {
    if (e.target.closest("#archive-open-doc")) {
      const id = state.selectedId;
      if (!id) return;
      try {
        await openDocumentFile(`/api/documents/${encodeURIComponent(id)}/file`);
      } catch (err) {
        toast(String(err.message || err), "error");
      }
      return;
    }
    if (e.target.closest("#archive-reveal-doc")) {
      const id = state.selectedId;
      const btn = document.getElementById("archive-reveal-doc");
      if (!id) return;
      if (btn) btn.disabled = true;
      try {
        await api(`/api/documents/${encodeURIComponent(id)}/reveal`, { method: "POST" });
      } catch (err) {
        toast(String(err.message || err), "error");
      } finally {
        if (btn) btn.disabled = false;
      }
    }
  });

  window.addEventListener("hashchange", () => {
    if (currentView() === "archive") hydrateFromHash();
  });

  if (currentView() === "archive") {
    state.applyingHash = true;
    state.filters = filtersFromHashQuery(parseHashQuery());
    writeToolbarFilters(state.filters);
    state.applyingHash = false;
  }
}
