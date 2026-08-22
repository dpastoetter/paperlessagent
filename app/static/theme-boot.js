/**
 * Apply stored / query theme before first paint (blocking; keep tiny).
 * Lives outside index.html so CSP can use script-src 'self' without unsafe-inline.
 */
(() => {
  try {
    const params = new URLSearchParams(window.location.search);
    const theme = params.get("theme") || window.localStorage.getItem("dc-theme");
    if (theme) {
      document.documentElement.dataset.theme = theme;
    }
    const desktop =
      params.get("desktop") === "1" ||
      Boolean(window.matchMedia?.("(display-mode: standalone)")?.matches);
    if (desktop) {
      document.documentElement.classList.add("dc-desktop");
    }
  } catch {
    /* private mode / blocked storage */
  }
})();
