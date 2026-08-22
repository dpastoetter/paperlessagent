import { displayText, refreshHealth } from "./api.js";
import { hooks, workflowState } from "./state.js";

/** Minimum time a step stays visibly running before a terminal transition. */
const STEP_MIN_VISIBLE_MS = 180;

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

function stepHoverText(step, detail = "", status = "idle") {
  const desc = step.description || step.label;
  if (status === "running" && detail) {
    return `${desc} — ${detail}`;
  }
  return desc;
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
    node.dataset.tip = stepHoverText(step);

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
  const tip = stepHoverText(step, detail, status);
  const nodeEl = stepEl.querySelector(".pipeline-node");
  if (nodeEl) {
    nodeEl.dataset.tip = tip;
  }
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
    const detail = displayText(workflowState.stepDetail[runningStep.id], "");
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
    const detail = displayText(workflowState.stepDetail[errorStep.id], "Step failed");
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

  // Keep active queue row elapsed fresh without remounting.
  if (workflowState.activeFileId) {
    patchQueueRow(workflowState.activeFileId);
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

function isQueueItemCancellable(item) {
  if (!item) return false;
  if (item.status === "running") return true;
  if (item.file_id !== workflowState.activeFileId) return false;
  return workflowState.steps.some(
    (step) => workflowState.stepStatus[step.id] === "running",
  );
}

function updateQueueRowActions(li, item) {
  let actions = li.querySelector(".queue-actions");
  if (!actions) {
    actions = document.createElement("span");
    actions.className = "queue-actions";
    li.appendChild(actions);
  }
  actions.replaceChildren();
  if (isQueueItemCancellable(item)) {
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

function queueElapsedText(item) {
  if (!item || item.status !== "running") return "";
  if (item.file_id !== workflowState.activeFileId) return "";
  const runningStep = workflowState.steps.find(
    (step) => workflowState.stepStatus[step.id] === "running",
  );
  if (!runningStep) return "";
  const started = workflowState.stepStartedAt[runningStep.id];
  if (!started) return "";
  return formatElapsed(performance.now() - started);
}

function fillQueueRowContent(li, item) {
  let main = li.querySelector(".queue-main");
  if (!main) {
    main = document.createElement("div");
    main.className = "queue-main";
    li.prepend(main);
  }

  let nameEl = main.querySelector(".queue-name");
  if (!nameEl) {
    nameEl = document.createElement("span");
    nameEl.className = "queue-name";
    main.appendChild(nameEl);
  }
  nameEl.textContent = item.filename || "document";

  let metaEl = main.querySelector(".queue-meta");
  if (!metaEl) {
    metaEl = document.createElement("span");
    metaEl.className = "queue-meta";
    main.appendChild(metaEl);
  }
  const status = queueStatusLabel(item);
  const elapsed = queueElapsedText(item);
  metaEl.textContent = elapsed ? `${status} · ${elapsed}` : status;
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
    fillQueueRowContent(li, item);
    updateQueueRowActions(li, item);
    root.appendChild(li);
  }
}

export function patchQueueRow(fileId) {
  if (!fileId) return;
  const root = document.getElementById("job-queue");
  const item = workflowState.queue.find((q) => q.file_id === fileId);
  if (!root || !item) return;

  const li = root.querySelector(`[data-file-id="${fileId}"]`);
  if (!li) return;

  li.dataset.status = item.status || "queued";
  fillQueueRowContent(li, item);
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
    fillQueueRowContent(li, item);
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

export function resetStepStatuses(mode = "idle") {
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

export function renderWorkflow() {
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

/** Refresh the review queue as soon as a file is waiting, not only when the job ends. */
export function shouldRefreshReviews(event) {
  if (!event) return false;
  if (event.type === "file_finished" && event.status === "review") return true;
  if (event.type === "job_finished") return true;
  return false;
}

function handleWorkflowEvent(event) {
  const type = event.type;
  if (shouldRefreshReviews(event) && type === "file_finished") {
    hooks.refreshReviews().catch(() => {});
  }
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
    hooks.setProcessInboxBusy(true);
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
    patchQueueRow(event.file_id);
    renderWorkflowNow();
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
    hooks.setProcessInboxBusy(false);
    if (event.status === "empty") {
      workflowState.queue = [];
      workflowState.activeFileId = null;
      workflowState.activeFilename = null;
      workflowState.jobTotal = 0;
      resetStepStatuses("idle");
    }
    renderWorkflow();
    hooks.refreshInbox().catch(() => {});
    hooks.refreshDocs().catch(() => {});
    hooks.refreshReviews().catch(() => {});
    refreshHealth().catch(() => {});
  }
}

export function connectWorkflowEvents() {
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

export function initWorkflowEvents() {
  // Cross-module refresh/busy hooks are wired in app.js via state.hooks.
}
