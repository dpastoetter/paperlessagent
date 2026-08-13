/**
 * Apply stored / query theme before first paint (blocking; keep tiny).
 * Lives outside index.html so CSP can use script-src 'self' without unsafe-inline.
 */
(() => {
  try {
    const params = new URLSearchParams(window.location.search);
    const theme = params.get("theme") || window.localStorage.getItem("pa-theme");
    if (theme) {
      document.documentElement.dataset.theme = theme;
    }
  } catch {
    /* private mode / blocked storage */
  }
})();
