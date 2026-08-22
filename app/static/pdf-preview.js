import { escapeHtml } from "./api.js";
import * as pdfjs from "./vendor/pdfjs/pdf.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "./vendor/pdfjs/pdf.worker.mjs",
  import.meta.url,
).href;

/**
 * Render a PDF into `container` with PDF.js (fit-to-width canvases).
 * `source` is an ArrayBuffer/Uint8Array or same-origin URL string.
 * Returns an unmount function that cancels in-flight work and frees the doc.
 */
export function mountPdfPreview(container, source, options = {}) {
  if (!container) return () => {};

  const scrollClass = options.scrollClass || "pdf-preview-scroll";
  const pageClass = options.pageClass || "pdf-preview-page";

  const docInit =
    source instanceof ArrayBuffer
      ? { data: source }
      : source instanceof Uint8Array
        ? { data: source }
        : { url: source };

  let cancelled = false;
  let debounceTimer = 0;
  let resizeObserver = null;
  /** @type {import("./vendor/pdfjs/pdf.mjs").PDFDocumentProxy | null} */
  let pdfDoc = null;
  let renderGeneration = 0;

  const scroll = document.createElement("div");
  scroll.className = scrollClass;
  scroll.setAttribute("role", "document");
  container.replaceChildren(scroll);
  scroll.innerHTML = `<div class="pdf-preview-loading">Loading preview…</div>`;

  const showError = (err) => {
    scroll.innerHTML = `<div class="review-preview-placeholder review-preview-error">
      <p>Could not render PDF preview</p>
      <p class="fine">${escapeHtml(String(err?.message || err || "Unknown error"))}</p>
    </div>`;
  };

  const render = async () => {
    const generation = ++renderGeneration;
    const width = container.clientWidth;
    if (width < 48) return;

    scroll.replaceChildren();
    scroll.innerHTML = `<div class="pdf-preview-loading">Loading preview…</div>`;

    try {
      if (!pdfDoc) {
        const task = pdfjs.getDocument(docInit);
        pdfDoc = await task.promise;
      }
      if (cancelled || generation !== renderGeneration) return;

      scroll.replaceChildren();
      const pageCount = pdfDoc.numPages;

      for (let pageNum = 1; pageNum <= pageCount; pageNum += 1) {
        if (cancelled || generation !== renderGeneration) return;

        const page = await pdfDoc.getPage(pageNum);
        const base = page.getViewport({ scale: 1 });
        const scale = Math.max(0.1, (width - 8) / base.width);
        const viewport = page.getViewport({ scale });

        const canvas = document.createElement("canvas");
        canvas.className = pageClass;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.setAttribute(
          "aria-label",
          pageCount > 1 ? `Page ${pageNum} of ${pageCount}` : "Document page",
        );

        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("Canvas is not available");

        await page.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled || generation !== renderGeneration) return;
        scroll.appendChild(canvas);
      }
    } catch (err) {
      if (cancelled || generation !== renderGeneration) return;
      showError(err);
    }
  };

  const schedule = () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      render().catch((err) => {
        if (!cancelled) showError(err);
      });
    }, 80);
  };

  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(schedule);
    resizeObserver.observe(container);
  }
  schedule();
  requestAnimationFrame(() => requestAnimationFrame(schedule));

  return () => {
    cancelled = true;
    renderGeneration += 1;
    window.clearTimeout(debounceTimer);
    resizeObserver?.disconnect();
    if (pdfDoc) {
      pdfDoc.destroy().catch(() => {});
      pdfDoc = null;
    }
    scroll.replaceChildren();
  };
}
