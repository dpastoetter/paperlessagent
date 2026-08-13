import {
  ensureBrowserSession,
  initSessionUnlock,
  refreshAuth,
  refreshHealth,
  startHealthPolling,
} from "./api.js";
import { hooks } from "./state.js";
import { initRouter, renderRoute } from "./router.js";
import {
  initInbox,
  refreshInbox,
  setProcessInboxBusy,
} from "./inbox.js";
import { initReview, refreshReviews } from "./review.js";
import { initDocuments, refreshDocs } from "./documents.js";
import { initAsk, renderAskResult } from "./ask.js";
import {
  connectWorkflowEvents,
  initWorkflowEvents,
  renderWorkflow,
  resetStepStatuses,
} from "./events.js";
import {
  applyMockScene,
  initSettings,
  initTheme,
  refreshAutostart,
  refreshSetup,
  refreshUpdateVersion,
  setSetupStatus,
} from "./settings.js";

hooks.setProcessInboxBusy = setProcessInboxBusy;
hooks.refreshInbox = refreshInbox;
hooks.refreshDocs = refreshDocs;
hooks.refreshReviews = refreshReviews;
hooks.refreshHealth = refreshHealth;
hooks.renderAskResult = renderAskResult;
hooks.renderWorkflow = renderWorkflow;

initRouter();
initInbox();
initReview();
initDocuments();
initAsk();
initSettings();
initWorkflowEvents();

/* ————— Boot (same sequence as the former monolithic app.js) ————— */

renderRoute();
initTheme();
initSessionUnlock();
resetStepStatuses("idle");
renderWorkflow();
document.getElementById("mock-toggle").checked = Boolean(window.PA_MOCK?.enabled);
if (window.PA_MOCK?.enabled) {
  applyMockScene();
} else {
  connectWorkflowEvents();
}

ensureBrowserSession().then((ready) => {
  if (!ready) return;
  refreshHealth()
    .then((health) => {
      if (health?.llm_provider === "ollama") return null;
      return refreshAuth();
    })
    .catch(() => {});
  startHealthPolling();
  refreshInbox().catch(() => {});
  refreshDocs().catch(() => {});
  refreshReviews().catch(() => {});
  refreshUpdateVersion().catch(() => {});
  refreshSetup()
    .catch((err) => {
      setSetupStatus(String(err.message || err), "err");
    })
    .finally(() => {
      // Re-render once categories are known so review cards get full select options.
      refreshReviews().catch(() => {});
    });
  refreshAutostart().catch(() => {});
});
