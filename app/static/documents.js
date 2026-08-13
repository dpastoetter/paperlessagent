import { api, escapeHtml, toast } from "./api.js";

export function docsEmptyState(message) {
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

export async function refreshDocs(params = {}) {
  const qs = new URLSearchParams(params);
  const data = await api(`/api/documents?${qs.toString()}`);
  renderDocs(data);
}

export function initDocuments() {
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
}
