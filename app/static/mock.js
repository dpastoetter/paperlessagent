/*
 * Mockup mode: fills every view with canned demo data for screenshots.
 *
 * Client-side only — while enabled, api() never talks to the backend and all
 * mutating actions are blocked, so real documents and settings are untouched.
 * Enable via the Settings toggle (persisted) or a ?mock=1 URL parameter.
 */
(function () {
  const param = new URLSearchParams(window.location.search).get("mock");
  let stored = null;
  try {
    stored = localStorage.getItem("pa-mock");
  } catch (_err) {
    // private mode
  }
  const enabled = param !== null ? param !== "0" : stored === "1";

  const DOCS = [
    {
      id: "demo-doc-1",
      filename: "2024-06-03_Invoice_AcmeCorp_EUR1240.pdf",
      original_name: "scan_20240603_1412.pdf",
      doc_type: "invoice",
      doc_date: "2024-06-03",
      subject: "Consulting services May 2024",
      counterparties: "Acme Corp",
      amount: 1240.0,
      currency: "EUR",
      summary:
        "Invoice #2024-118 from Acme Corp for consulting services in May, total EUR 1,240.00 incl. VAT, due 2024-06-17.",
    },
    {
      id: "demo-doc-2",
      filename: "2024-05-28_Contract_Stadtwerke.pdf",
      original_name: "vertrag_stadtwerke.pdf",
      doc_type: "contract",
      doc_date: "2024-05-28",
      subject: "Electricity supply agreement",
      counterparties: "Stadtwerke München",
      amount: null,
      currency: null,
      summary:
        "Electricity supply contract with Stadtwerke München, base price EUR 12.90/month, 24-month term starting July 2024.",
    },
    {
      id: "demo-doc-3",
      filename: "2024-05-12_Tax_Finanzamt_EUR842.pdf",
      original_name: "steuerbescheid_2023.pdf",
      doc_type: "tax",
      doc_date: "2024-05-12",
      subject: "Income tax assessment 2023",
      counterparties: "Finanzamt München",
      amount: 842.0,
      currency: "EUR",
      summary:
        "Income tax assessment for 2023 with a refund of EUR 842.00, transferred to the account on file.",
    },
    {
      id: "demo-doc-4",
      filename: "2024-04-19_Medical_MRI-right-knee_Dr-Weber.pdf",
      original_name: "befund_scan.pdf",
      doc_type: "medical",
      doc_date: "2024-04-19",
      subject: "MRI right knee results",
      counterparties: "Dr. Weber",
      amount: null,
      currency: null,
      summary:
        "Radiology report from Dr. Weber: MRI of the right knee, no structural damage, physiotherapy recommended.",
    },
    {
      id: "demo-doc-5",
      filename: "2024-03-30_Receipt_Bauhaus_EUR67p80.jpg",
      original_name: "IMG_2041.jpg",
      doc_type: "receipt",
      doc_date: "2024-03-30",
      subject: "Paint and brushes purchase",
      counterparties: "Bauhaus",
      amount: 67.8,
      currency: "EUR",
      summary: "Hardware store receipt from Bauhaus: paint, brushes, and masking tape, EUR 67.80 total.",
    },
  ];

  const REVIEWS = [
    {
      id: "demo-review-1",
      source_path: "/home/demo/Paperless/inbox/scan_20240614_0932.pdf",
      original_name: "scan_20240614_0932.pdf",
      status: "pending",
      created_at: "2024-06-14T09:32:00Z",
      proposal: {
        filename: "2024-06-14_Invoice_AcmeCorp_EUR380.pdf",
        doc_type: "invoice",
        doc_date: "2024-06-14",
        subject: "On-site support June 12",
        counterparties: "Acme Corp",
        amount: 380.0,
        currency: "EUR",
        summary:
          "Invoice #2024-131 from Acme Corp for on-site support on June 12, total EUR 380.00 incl. VAT.",
      },
      duplicates: [
        {
          kind: "similar",
          document_id: "demo-doc-1",
          filename: "2024-06-03_Invoice_AcmeCorp_EUR1240.pdf",
          score: 0.86,
        },
      ],
    },
  ];

  const SETTINGS = {
    source_dir: "/home/demo/Paperless/inbox",
    categories: [
      "invoice",
      "receipt",
      "contract",
      "letter",
      "tax",
      "medical",
      "id",
      "other",
    ].map((name) => ({ name, folder: `/home/demo/Paperless/archive/${name}` })),
    batch: { poll_interval_seconds: 30 },
    review: { require_approval: true },
  };

  const ASK = {
    status: "success",
    reply:
      "You received two invoices from Acme Corp this quarter. The larger one is invoice #2024-118 from June 3 over EUR 1,240.00 for consulting services in May (due June 17). A second invoice over EUR 380.00 for on-site support is currently waiting in your review queue and has not been filed yet.",
    sources: [
      { document_id: "demo-doc-1", filename: "2024-06-03_Invoice_AcmeCorp_EUR1240.pdf" },
    ],
  };

  const AUTH = {
    status: "success",
    auth_mode: "chatgpt_oauth",
    openai_ready: true,
    chatgpt_email: "demo@paperless.app",
    chatgpt_plan: "plus",
  };

  const OLLAMA = {
    active: false,
    ready: false,
    reachable: true,
    listening: true,
    can_start: false,
    binary: "/usr/local/bin/ollama",
    base_url: "http://localhost:11434",
    version: "0.6.0",
    installed_models: ["gemma3:latest", "nomic-embed-text:latest"],
    chat_model: "gemma3",
    embedding_model: "nomic-embed-text",
    missing_models: [],
    pull_command: "",
    error: null,
    install_hint: null,
  };

  const ROUTES = [
    [/^\/api\/health/, () => ({
      status: "ok",
      llm_provider: "openai",
      model: "gpt-5",
      auth: AUTH,
      usage: {
        requests: 12,
        chat_requests: 10,
        embed_requests: 2,
        prompt_tokens: 8400,
        completion_tokens: 2100,
        total_tokens: 10500,
        last_provider: "openai",
        last_model: "gpt-5",
        last_kind: "chat",
        updated_at: "2026-01-01T00:00:00+00:00",
      },
    })],
    [/^\/api\/auth\/status/, () => ({ ...AUTH, cloud_disclaimer: { version: "1", accepted: true, accepted_at: "2026-01-01T00:00:00+00:00" } })],
    [
      /^\/api\/privacy\/cloud-disclaimer/,
      () => ({
        status: "success",
        cloud_disclaimer: { version: "1", accepted: true, accepted_at: "2026-01-01T00:00:00+00:00" },
      }),
    ],
    [/^\/api\/ollama\/status/, () => ({ status: "success", ollama: OLLAMA })],
    [
      /^\/api\/ollama\/enable/,
      () => ({
        status: "success",
        applied: { provider: "ollama", model: "gemma3", embedding_model: "nomic-embed-text" },
        ollama: { ...OLLAMA, active: true, ready: true },
      }),
    ],
    [
      /^\/api\/ollama\/start/,
      () => ({
        status: "success",
        started: true,
        already_running: false,
        method: "ollama serve",
        ollama: { ...OLLAMA, active: true, ready: true, can_start: false },
      }),
    ],
    [
      /^\/api\/ollama\/pull/,
      () => ({ status: "success", model: "gemma3", ollama: { ...OLLAMA, active: true, ready: true } }),
    ],
    [
      /^\/api\/ollama\/ps/,
      () => ({
        status: "success",
        models: [{ name: "gemma3:4b", size: 2800000000, size_vram: 2800000000 }],
      }),
    ],
    [
      /^\/api\/ollama\/unload/,
      () => ({ status: "success", unloaded: ["gemma3:4b"], running_models: [] }),
    ],
    [
      /^\/api\/ollama\/restart/,
      () => ({
        status: "success",
        restarted: true,
        method: "systemctl --user restart ollama.service",
        ollama: { ...OLLAMA, active: true, ready: true },
      }),
    ],
    [
      /^\/api\/process\/cancel/,
      () => ({ status: "success", file_id: "demo-f2", message: "Cancellation requested" }),
    ],
    [
      /^\/api\/process\/retry/,
      () => ({ status: "success", cancelled: true, message: "Cancellation requested for the active file." }),
    ],
    [
      /^\/api\/llm\/provider/,
      () => ({
        status: "success",
        applied: { provider: "openai", model: "gpt-5.6-luna", embedding_model: "text-embedding-3-small" },
      }),
    ],
    [
      /^\/api\/inbox/,
      () => ({
        status: "success",
        count: 2,
        source_dir: SETTINGS.source_dir,
        files: [
          { name: "scan_20240614_0932.pdf", path: `${SETTINGS.source_dir}/scan_20240614_0932.pdf`, suffix: ".pdf", size_bytes: 482113 },
          { name: "receipt_okt.jpg", path: `${SETTINGS.source_dir}/receipt_okt.jpg`, suffix: ".jpg", size_bytes: 201422 },
        ],
      }),
    ],
    [/^\/api\/documents\?|^\/api\/documents$/, () => ({ status: "success", count: DOCS.length, documents: DOCS })],
    [/^\/api\/reviews$/, () => ({ status: "success", count: REVIEWS.length, reviews: REVIEWS })],
    [/^\/api\/settings$/, () => ({ status: "success", settings: SETTINGS })],
    [
      /^\/api\/update\/status/,
      (path) =>
        path.includes("check=true")
          ? {
              status: "success",
              repo: "dpastoetter/paperlessagent",
              current_version: "0.4.2",
              latest_version: "0.4.2",
              update_available: false,
            }
          : { status: "success", repo: "dpastoetter/paperlessagent", current_version: "0.4.2" },
    ],
    [/^\/api\/ask$/, () => ASK],
  ];

  async function respond(path, options = {}) {
    for (const [pattern, handler] of ROUTES) {
      if (pattern.test(path)) {
        // Small delay so spinners/status lines render naturally.
        await new Promise((r) => setTimeout(r, 120));
        return handler(path, options);
      }
    }
    throw new Error("Mockup mode is on — this action is disabled. Turn it off in Settings.");
  }

  function setEnabled(value) {
    try {
      localStorage.setItem("pa-mock", value ? "1" : "0");
    } catch (_err) {
      // private mode — toggle just won't persist
    }
  }

  window.PA_MOCK = {
    enabled,
    respond,
    setEnabled,
    ask: ASK,
    // Frozen mid-run pipeline scene for the inbox workflow panel.
    workflow: {
      activeFilename: "scan_20240614_0932.pdf",
      activeFileId: "demo-f2",
      jobTotal: 3,
      stepStatus: {
        read: "done",
        ai_ocr: "done",
        extract: "running",
        name: "wait",
        review: "wait",
        file: "wait",
        index: "wait",
      },
      stepDetail: {
        read: "2 pages · 1840 chars in text layer",
        ai_ocr: "2 pages · 3120 chars",
        extract: "Extracting type, date, subject, parties via local Ollama · gemma3 — can take a while",
      },
      queue: [
        { file_id: "demo-f1", filename: "invoice_mai.pdf", status: "done", stepLabel: "Done" },
        { file_id: "demo-f2", filename: "scan_20240614_0932.pdf", status: "running", stepLabel: "Find details" },
        { file_id: "demo-f3", filename: "receipt_okt.jpg", status: "queued", stepLabel: null },
      ],
    },
  };
})();
