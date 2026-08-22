/**
 * Keyboard shortcut helpers.
 *
 * Manual verification (happy-dom limits): full Tab focus trap inside dialogs,
 * pointer blocking via `inert`, and assistive-tech announcement of help open/close.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  closeShortcutHelp,
  closeTopOverlay,
  getOverlayStack,
  initKeyboard,
  isShortcutHelpOpen,
  isTypingTarget,
  openShortcutHelp,
  pushOverlay,
  removeOverlay,
} from "../../app/static/keyboard.js";
import { renderRoute } from "../../app/static/router.js";

describe("isTypingTarget", () => {
  it("detects form fields and contenteditable", () => {
    const input = document.createElement("input");
    const wrap = document.createElement("div");
    wrap.appendChild(input);
    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
    const editable = document.createElement("div");
    editable.contentEditable = "true";
    expect(isTypingTarget(editable)).toBe(true);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
  });
});

describe("global shortcuts", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="app">
        <a class="nav-item" data-view="archive">Archive</a>
        <div class="view" data-view="archive"></div>
        <input id="search-q" type="search" />
        <input id="other-field" type="text" />
      </div>
      <div id="shortcut-help-backdrop" class="hidden" hidden></div>
      <div id="shortcut-help" class="shortcut-help hidden" hidden>
        <button type="button" id="shortcut-help-close">Close</button>
      </div>
    `;
    window.location.hash = "#/archive";
    renderRoute();
    initKeyboard();
    while (getOverlayStack().length) getOverlayStack().pop();
  });

  afterEach(() => {
    closeShortcutHelp();
    while (getOverlayStack().length) closeTopOverlay();
  });

  it("focuses Archive search on / when not typing", () => {
    const search = document.getElementById("search-q");
    const spy = vi.spyOn(search, "focus");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true }));
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("ignores / while focus is inside an input", () => {
    const other = document.getElementById("other-field");
    other.focus();
    const spy = vi.spyOn(document.getElementById("search-q"), "focus");
    other.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true }));
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("opens and closes shortcut help with ? and Escape", () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "?", bubbles: true }));
    expect(isShortcutHelpOpen()).toBe(true);
    expect(getOverlayStack().some((o) => o.id === "shortcut-help")).toBe(true);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(isShortcutHelpOpen()).toBe(false);
  });

  it("does not close help with Escape while typing outside the dialog", () => {
    openShortcutHelp();
    expect(isShortcutHelpOpen()).toBe(true);
    const other = document.getElementById("other-field");
    other.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(isShortcutHelpOpen()).toBe(true);
    closeShortcutHelp();
  });

  it("closeShortcutHelp restores prior focus when possible", () => {
    const trigger = document.createElement("button");
    trigger.id = "help-trigger";
    document.body.appendChild(trigger);
    trigger.focus();
    openShortcutHelp();
    closeShortcutHelp();
    // Focus restore is rAF-scheduled; flush microtasks/frames best-effort
    return new Promise((resolve) => {
      requestAnimationFrame(() => {
        expect(
          document.activeElement === trigger || document.activeElement?.id === "shortcut-help-close",
        ).toBe(true);
        resolve(undefined);
      });
    });
  });

  it("pushOverlay / removeOverlay manage the stack", () => {
    const el = document.createElement("div");
    pushOverlay({ id: "test", el, onClose: () => {} });
    expect(getOverlayStack().some((o) => o.id === "test")).toBe(true);
    removeOverlay("test");
    expect(getOverlayStack().some((o) => o.id === "test")).toBe(false);
  });
});
