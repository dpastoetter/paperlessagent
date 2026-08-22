export const VIEWS = ["inbox", "review", "archive", "ask", "settings"];

export function currentView() {
  const name = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return VIEWS.includes(name) ? name : "inbox";
}

/** Parse `?key=value` from the location hash (`#/archive?q=Acme`). */
export function parseHashQuery() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const qIndex = raw.indexOf("?");
  if (qIndex < 0) return {};
  const params = new URLSearchParams(raw.slice(qIndex + 1));
  const out = {};
  for (const [key, value] of params.entries()) {
    if (value !== "") out[key] = value;
  }
  return out;
}

/**
 * Write `#/{view}?…` omitting empty values.
 * Uses `location.replace` when only query changes on the same view to avoid
 * stacking identical history entries for every keystroke debounce.
 */
export function setHashQuery(view, params = {}, { replace = true } = {}) {
  const name = VIEWS.includes(view) ? view : "inbox";
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value == null) continue;
    const text = String(value).trim();
    if (!text) continue;
    qs.set(key, text);
  }
  const suffix = qs.toString();
  const next = suffix ? `#/${name}?${suffix}` : `#/${name}`;
  if (window.location.hash === next) return;
  if (replace) {
    const url = `${window.location.pathname}${window.location.search}${next}`;
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = next;
  }
}

export function renderRoute() {
  const view = currentView();
  for (const el of document.querySelectorAll(".view")) {
    el.classList.toggle("active", el.dataset.view === view);
  }
  for (const el of document.querySelectorAll(".nav-item")) {
    const active = el.dataset.view === view;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  }
  document.title = `${view[0].toUpperCase()}${view.slice(1)} · DeepCatalog Studio`;
}

export function initRouter() {
  window.addEventListener("hashchange", renderRoute);
}
