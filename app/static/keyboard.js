/**
 * Shared typing detection, overlay stack, and global shortcuts (/ ? Escape).
 *
 * Manual QA (happy-dom cannot fully prove): Tab focus trap inside dialogs,
 * inert background blocking pointer interaction, and screen-reader announcement
 * of shortcut-help open/close.
 */

import { currentView } from "./router.js";

/** @typedef {{ id: string, el: HTMLElement|null, onClose: () => void, restoreFocus?: HTMLElement|null }} OverlayEntry */

/** @type {OverlayEntry[]} */
const overlayStack = [];

let helpRestoreFocus = null;
let keyboardInitialized = false;

export function isTypingTarget(el) {
  if (!el || !(el instanceof Element)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return Boolean(el.closest("input, textarea, select, [contenteditable='true']"));
}

export function getOverlayStack() {
  return overlayStack;
}

/**
 * Mark page chrome inert while an overlay is open.
 * @param {boolean} active
 * @param {{ mode?: 'shell' | 'archive-drawer' }} [opts]
 */
export function setBackgroundInert(active, { mode = "shell" } = {}) {
  const app = document.querySelector(".app");
  const sidebar = document.querySelector(".sidebar");
  const toast = document.getElementById("toast-stack");
  const viewArchive = document.getElementById("view-archive");

  if (mode === "shell") {
    if (app) app.inert = active;
    if (toast) toast.inert = active;
    if (sidebar) sidebar.inert = false;
    if (viewArchive) {
      for (const child of viewArchive.children) {
        child.inert = false;
      }
    }
    return;
  }

  // Archive drawer lives inside .app — inert siblings, keep drawer interactive.
  if (app) app.inert = false;
  if (toast) toast.inert = active;
  if (sidebar) sidebar.inert = active;
  if (viewArchive) {
    for (const child of viewArchive.children) {
      if (child.id === "archive-drawer" || child.id === "archive-drawer-backdrop") {
        child.inert = false;
      } else {
        child.inert = active;
      }
    }
  }
}

export function pushOverlay(entry) {
  const existing = overlayStack.findIndex((item) => item.id === entry.id);
  if (existing >= 0) overlayStack.splice(existing, 1);
  overlayStack.push(entry);
}

export function removeOverlay(id) {
  const idx = overlayStack.findIndex((item) => item.id === id);
  if (idx >= 0) overlayStack.splice(idx, 1);
}

export function closeTopOverlay() {
  const top = overlayStack[overlayStack.length - 1];
  if (!top) return false;
  overlayStack.pop();
  top.onClose();
  return true;
}

export function isShortcutHelpOpen() {
  const dialog = document.getElementById("shortcut-help");
  return Boolean(dialog && !dialog.hidden && !dialog.classList.contains("hidden"));
}

export function openShortcutHelp() {
  const dialog = document.getElementById("shortcut-help");
  const backdrop = document.getElementById("shortcut-help-backdrop");
  if (!dialog || isShortcutHelpOpen()) return;
  helpRestoreFocus =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  dialog.hidden = false;
  dialog.classList.remove("hidden");
  if (backdrop) {
    backdrop.hidden = false;
    backdrop.classList.remove("hidden");
  }
  setBackgroundInert(true, { mode: "shell" });
  pushOverlay({
    id: "shortcut-help",
    el: dialog,
    onClose: () => closeShortcutHelp({ fromStack: true }),
    restoreFocus: helpRestoreFocus,
  });
  window.requestAnimationFrame(() => {
    document.getElementById("shortcut-help-close")?.focus();
  });
}

export function closeShortcutHelp({ fromStack = false } = {}) {
  const dialog = document.getElementById("shortcut-help");
  const backdrop = document.getElementById("shortcut-help-backdrop");
  if (!dialog) return;
  const wasOpen = isShortcutHelpOpen();
  dialog.hidden = true;
  dialog.classList.add("hidden");
  if (backdrop) {
    backdrop.hidden = true;
    backdrop.classList.add("hidden");
  }
  if (!fromStack) removeOverlay("shortcut-help");
  // Always clear shell inert so a half-closed help dialog cannot freeze the UI.
  setBackgroundInert(false, { mode: "shell" });
  if (!wasOpen) return;
  const restore = helpRestoreFocus;
  helpRestoreFocus = null;
  window.requestAnimationFrame(() => {
    restore?.focus?.();
  });
}

export function registerArchiveDrawerOverlay({ open, onClose, restoreFocus = null } = {}) {
  if (open) {
    setBackgroundInert(true, { mode: "archive-drawer" });
    pushOverlay({
      id: "archive-drawer",
      el: document.getElementById("archive-drawer"),
      onClose: () => {
        onClose?.();
      },
      restoreFocus,
    });
  } else {
    removeOverlay("archive-drawer");
    setBackgroundInert(false, { mode: "archive-drawer" });
  }
}

function onGlobalKeydown(e) {
  if (e.key === "Escape") {
    const top = overlayStack[overlayStack.length - 1];
    if (!top) return;
    const typing = isTypingTarget(e.target);
    const inside = top.el instanceof Element && top.el.contains(/** @type {Node} */ (e.target));
    if (typing && !inside) return;
    e.preventDefault();
    closeTopOverlay();
    return;
  }

  if (isTypingTarget(e.target)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
    e.preventDefault();
    if (isShortcutHelpOpen()) closeShortcutHelp();
    else openShortcutHelp();
    return;
  }

  if (e.key === "/") {
    if (currentView() !== "archive") return;
    e.preventDefault();
    document.getElementById("search-q")?.focus();
  }
}

export function initKeyboard() {
  if (keyboardInitialized) return;
  keyboardInitialized = true;
  // Defensive: never leave overlays/inert stuck across hot reload or partial boots.
  closeShortcutHelp();
  document.addEventListener("keydown", onGlobalKeydown);
  document.getElementById("shortcut-help-close")?.addEventListener("click", () => {
    closeShortcutHelp();
  });
  document.getElementById("shortcut-help-backdrop")?.addEventListener("click", () => {
    closeShortcutHelp();
  });
}
