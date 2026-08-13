import {
  api,
  armedConfirm,
  errorMessage,
  setText,
  toast,
} from "./api.js";
import { hooks, workflowState } from "./state.js";
import { patchQueueRow } from "./events.js";

export function setProcessStatus(message, tone = "") {
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

export async function refreshInbox() {
  const data = await api("/api/inbox");
  setText("inbox-out", data);
  renderInboxSummary(data);
  return data;
}

export function setProcessInboxBusy(busy) {
  const btn = document.getElementById("process-inbox");
  if (btn) btn.disabled = Boolean(busy);
}

export function initInbox() {
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

  document.getElementById("job-queue")?.addEventListener("click", async (ev) => {
    const cancelBtn = ev.target.closest(".queue-cancel");
    if (cancelBtn) {
      const fileId = cancelBtn.dataset.fileId;
      if (!fileId) return;
      cancelBtn.disabled = true;
      const prevLabel = cancelBtn.textContent;
      cancelBtn.textContent = "Cancelling…";
      try {
        await api("/api/process/cancel", {
          method: "POST",
          body: { file_id: fileId },
        });
        toast("Cancellation requested", "ok");
        const row = workflowState.queue.find((q) => q.file_id === fileId);
        if (row) {
          row.stepLabel = "Cancelling…";
          patchQueueRow(fileId);
        }
      } catch (err) {
        cancelBtn.disabled = false;
        cancelBtn.textContent = prevLabel;
        toast(errorMessage(err, "Could not cancel this file"), "error");
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
          body: { path },
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
      await hooks.refreshDocs();
      await hooks.refreshReviews().catch(() => {});
    } catch (err) {
      const msg = String(err.message || err);
      setText("process-out", msg);
      setProcessStatus(msg, "error");
      toast(msg, "error");
    } finally {
      setProcessInboxBusy(false);
    }
  });
}
