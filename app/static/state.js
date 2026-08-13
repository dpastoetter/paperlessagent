/** Shared mutable UI state — breaks circular imports between feature modules. */

export let knownCategories = [];

export function setKnownCategories(names) {
  knownCategories = Array.isArray(names) ? names : [];
}

export const DEFAULT_PIPELINE_STEPS = [
  {
    id: "read",
    label: "Open file",
    description: "Load the scan and read any embedded PDF text layer.",
  },
  {
    id: "ai_ocr",
    label: "Transcribe",
    description: "Use AI vision to read each page image and recover the text.",
  },
  {
    id: "extract",
    label: "Find details",
    description: "Pull out dates, parties, amounts, and other metadata with the LLM.",
  },
  {
    id: "name",
    label: "Name file",
    description: "Propose a clear filename from the extracted details.",
  },
  {
    id: "review",
    label: "Review",
    description: "Pause for your approval before anything is written to disk.",
  },
  {
    id: "file",
    label: "Save",
    description: "Move the document into the archive folder for its category.",
  },
  {
    id: "index",
    label: "Make searchable",
    description: "Chunk the text and store embeddings so Ask can search it.",
  },
];

export const workflowState = {
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

/**
 * Cross-module callbacks filled by app.js after imports.
 * Avoids cycles: events↔inbox, inbox↔review, settings↔ask/events.
 */
export const hooks = {
  setProcessInboxBusy: (_busy) => {},
  refreshInbox: async () => {},
  refreshDocs: async () => {},
  refreshReviews: async () => {},
  refreshHealth: async () => {},
  renderAskResult: (_data) => {},
  renderWorkflow: () => {},
};
