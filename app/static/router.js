export const VIEWS = ["inbox", "review", "archive", "ask", "settings"];

export function currentView() {
  const name = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return VIEWS.includes(name) ? name : "inbox";
}

export function renderRoute() {
  const view = currentView();
  for (const el of document.querySelectorAll(".view")) {
    el.classList.toggle("active", el.dataset.view === view);
  }
  for (const el of document.querySelectorAll(".nav-item")) {
    el.classList.toggle("active", el.dataset.view === view);
  }
  document.title = `${view[0].toUpperCase()}${view.slice(1)} · PaperlessAgent`;
}

export function initRouter() {
  window.addEventListener("hashchange", renderRoute);
}
