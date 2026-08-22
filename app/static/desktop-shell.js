/**
 * AppImage / native window: hide website behaviors (browser menus, tab shortcuts)
 * and send http(s) links to the system browser instead of a second WebKit view.
 */

import { isTypingTarget } from "./keyboard.js";

let desktopShellInitialized = false;
let originalWindowOpen = null;

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

export function isExternalHttpUrl(url) {
  try {
    const parsed = new URL(String(url), window.location.href);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    const host = parsed.hostname.toLowerCase();
    return host !== "127.0.0.1" && host !== "localhost" && host !== "::1";
  } catch {
    return false;
  }
}

export function openExternalHttpUrl(url) {
  if (!isExternalHttpUrl(url)) return false;
  const api = window.pywebview?.api;
  if (api && typeof api.open_url === "function") {
    Promise.resolve(api.open_url(url)).catch(() => {});
    return true;
  }
  if (originalWindowOpen) {
    originalWindowOpen(url, "_blank", "noopener");
    return true;
  }
  return false;
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

function onDesktopWindowOpen(url, target, features) {
  if (typeof url === "string" && isExternalHttpUrl(url) && openExternalHttpUrl(url)) {
    return null;
  }
  return originalWindowOpen ? originalWindowOpen(url, target, features) : null;
}

function onDesktopExternalLinkClick(event) {
  if (event.defaultPrevented) return;
  const link = event.target?.closest?.("a[href]");
  if (!link) return;
  const href = link.href;
  if (!isExternalHttpUrl(href)) return;
  if (link.target !== "_blank" && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    return;
  }
  event.preventDefault();
  openExternalHttpUrl(href);
}

export function initDesktopShell() {
  if (desktopShellInitialized) return;
  desktopShellInitialized = true;
  if (!applyDesktopShellClass()) return;
  document.addEventListener("contextmenu", onDesktopContextMenu);
  document.addEventListener("keydown", onDesktopBrowserKeys, true);
  document.addEventListener("click", onDesktopExternalLinkClick);
  if (typeof window.open === "function") {
    originalWindowOpen = window.open.bind(window);
    window.open = onDesktopWindowOpen;
  }
}
