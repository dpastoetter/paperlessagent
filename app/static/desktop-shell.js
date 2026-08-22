/**
 * AppImage / --app window: hide website behaviors (browser menus, tab shortcuts).
 */

import { isTypingTarget } from "./keyboard.js";

let desktopShellInitialized = false;

export function isDesktopShell() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("desktop") === "1") return true;
    return Boolean(window.matchMedia?.("(display-mode: standalone)")?.matches);
  } catch {
    return false;
  }
}

export function applyDesktopShellClass() {
  if (!isDesktopShell()) return false;
  document.documentElement.classList.add("dc-desktop");
  return true;
}

export function isBrowserChromeShortcut(event) {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const mod = event.ctrlKey || event.metaKey;
  if (key === "F12") return true;
  if (event.altKey && !mod) {
    return (
      key === "Home" ||
      key === "ArrowLeft" ||
      key === "ArrowRight" ||
      key === "d" ||
      key === "D"
    );
  }
  if (!mod) return false;
  if (event.shiftKey) {
    return (
      key === "n" ||
      key === "t" ||
      key === "p" ||
      key === "i" ||
      key === "j" ||
      key === "c" ||
      key === "k" ||
      key === "Delete"
    );
  }
  return (
    key === "n" ||
    key === "t" ||
    key === "l" ||
    key === "u" ||
    key === "d" ||
    key === "e" ||
    key === "s" ||
    key === "p"
  );
}

function onDesktopContextMenu(event) {
  if (isTypingTarget(event.target)) return;
  event.preventDefault();
}

function onDesktopBrowserKeys(event) {
  if (!isBrowserChromeShortcut(event)) return;
  event.preventDefault();
  event.stopPropagation();
}

export function initDesktopShell() {
  if (desktopShellInitialized) return;
  desktopShellInitialized = true;
  if (!applyDesktopShellClass()) return;
  document.addEventListener("contextmenu", onDesktopContextMenu);
  document.addEventListener("keydown", onDesktopBrowserKeys, true);
}
