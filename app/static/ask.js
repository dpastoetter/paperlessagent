import { api, escapeHtml, refreshHealth, toast } from "./api.js";

function stripSourcesSection(text) {
  return String(text || "")
    .replace(/\n*(?:#{1,3}\s*)?sources\b[\s\S]*$/i, "")
    .trim();
}

export function renderAskResult(data) {
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

export async function openDocumentFile(url) {
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

export function initAsk() {
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
}
