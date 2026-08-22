import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  applyDesktopShellClass,
  initDesktopShell,
  isBrowserChromeShortcut,
  isDesktopShell,
} from "../../app/static/desktop-shell.js";

function keyEvent(init) {
  return new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
}

describe("desktop shell detection", () => {
  afterEach(() => {
    document.documentElement.classList.remove("dc-desktop");
    window.history.replaceState(null, "", "/");
  });

  it("treats ?desktop=1 as the AppImage / --app shell", () => {
    window.history.replaceState(null, "", "/?desktop=1");
    expect(isDesktopShell()).toBe(true);
    expect(applyDesktopShellClass()).toBe(true);
    expect(document.documentElement.classList.contains("dc-desktop")).toBe(true);
  });

  it("does not mark a normal browser tab as the desktop shell", () => {
    window.history.replaceState(null, "", "/");
    expect(isDesktopShell()).toBe(false);
    expect(applyDesktopShellClass()).toBe(false);
    expect(document.documentElement.classList.contains("dc-desktop")).toBe(false);
  });
});

describe("browser chrome shortcuts", () => {
  it("blocks new-tab / address-bar / inspect keys", () => {
    expect(isBrowserChromeShortcut(keyEvent({ key: "t", ctrlKey: true }))).toBe(true);
    expect(isBrowserChromeShortcut(keyEvent({ key: "n", ctrlKey: true }))).toBe(true);
    expect(isBrowserChromeShortcut(keyEvent({ key: "l", ctrlKey: true }))).toBe(true);
    expect(isBrowserChromeShortcut(keyEvent({ key: "F12" }))).toBe(true);
    expect(isBrowserChromeShortcut(keyEvent({ key: "i", ctrlKey: true, shiftKey: true }))).toBe(
      true,
    );
  });

  it("leaves copy, undo, and in-app Ctrl+Enter alone", () => {
    expect(isBrowserChromeShortcut(keyEvent({ key: "c", ctrlKey: true }))).toBe(false);
    expect(isBrowserChromeShortcut(keyEvent({ key: "v", ctrlKey: true }))).toBe(false);
    expect(isBrowserChromeShortcut(keyEvent({ key: "z", ctrlKey: true }))).toBe(false);
    expect(isBrowserChromeShortcut(keyEvent({ key: "Enter", ctrlKey: true }))).toBe(false);
    expect(isBrowserChromeShortcut(keyEvent({ key: "r", ctrlKey: true }))).toBe(false);
  });
});

describe("initDesktopShell", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/?desktop=1");
    document.documentElement.classList.remove("dc-desktop");
  });

  afterEach(() => {
    window.history.replaceState(null, "", "/");
    document.documentElement.classList.remove("dc-desktop");
  });

  it("suppresses the page context menu outside fields", () => {
    initDesktopShell();
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    document.body.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
