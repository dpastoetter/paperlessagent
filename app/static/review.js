import {
  api,
  armedConfirm,
  escapeHtml,
  isFinancialDocType,
  referenceIdsToString,
  toast,
} from "./api.js";
import { knownCategories, hooks } from "./state.js";
import { openDocumentFile } from "./ask.js";

export function updateReviewBadge(count) {
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

function reviewFinancialFieldsHtml(p) {
  const show = isFinancialDocType(p.doc_type);
  return `<div class="review-fields-financial"${show ? "" : ' data-hidden="true"'}>
      <label class="field narrow">
        <span>Amount</span>
        <input type="number" step="0.01" class="rv-amount" value="${escapeHtml(p.amount ?? "")}" placeholder="0.00" />
      </label>
      <label class="field narrow">
        <span>Currency</span>
        <input type="text" class="rv-currency" value="${escapeHtml(p.currency || "")}" placeholder="EUR" maxlength="8" />
      </label>
    </div>`;
}

function syncReviewFinancialFields(card) {
  if (!card) return;
  const docType = card.querySelector(".rv-doc-type")?.value;
  const block = card.querySelector(".review-fields-financial");
  if (!block) return;
  const show = isFinancialDocType(docType);
  block.dataset.hidden = show ? "false" : "true";
  if (!show) {
    const amount = card.querySelector(".rv-amount");
    const currency = card.querySelector(".rv-currency");
    if (amount) amount.value = "";
    if (currency) currency.value = "";
  }
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
      <label class="field grow">
        <span>Reference numbers</span>
        <input type="text" class="rv-reference-ids" value="${escapeHtml(referenceIdsToString(p.reference_ids))}" placeholder="Invoice #, policy #, case ref…" />
      </label>
      ${reviewFinancialFieldsHtml(p)}
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

export async function refreshReviews() {
  const data = await api("/api/reviews");
  renderReviews(data);
  return data;
}

function collectReviewOverrides(card) {
  const docType = card.querySelector(".rv-doc-type")?.value || null;
  const refRaw = card.querySelector(".rv-reference-ids")?.value.trim() || "";
  const overrides = {
    filename: card.querySelector(".rv-filename")?.value.trim() || null,
    doc_type: docType,
    doc_date: card.querySelector(".rv-doc-date")?.value.trim() || null,
    subject: card.querySelector(".rv-subject")?.value.trim() || null,
    counterparties: card.querySelector(".rv-counterparties")?.value.trim() || null,
    reference_ids: refRaw
      ? refRaw.split(/[,;]+/).map((part) => part.trim()).filter(Boolean)
      : [],
    summary: card.querySelector(".rv-summary")?.value.trim() || null,
  };
  if (isFinancialDocType(docType)) {
    const amountRaw = card.querySelector(".rv-amount")?.value.trim();
    overrides.currency = card.querySelector(".rv-currency")?.value.trim() || null;
    if (amountRaw && !Number.isNaN(Number(amountRaw))) {
      overrides.amount = Number(amountRaw);
    }
  } else {
    overrides.amount = null;
    overrides.currency = null;
  }
  return overrides;
}

export function initReview() {
  document.getElementById("reviews").addEventListener("change", (e) => {
    if (e.target.matches(".rv-doc-type")) {
      syncReviewFinancialFields(e.target.closest(".review-card"));
    }
  });

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
        hooks.refreshDocs().catch(() => {});
        hooks.refreshInbox().catch(() => {});
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
        hooks.refreshInbox().catch(() => {});
      } catch (err) {
        toast(String(err.message || err), "error");
        btn.disabled = false;
      }
    }
  });

  document.getElementById("refresh-reviews").addEventListener("click", () => {
    refreshReviews().catch((err) => toast(String(err.message || err), "error"));
  });
}
