import { beforeEach, describe, expect, it } from "vitest";

import { VIEWS, currentView, initRouter, renderRoute } from "../../app/static/router.js";

describe("router", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="view" data-view="inbox"></div>
      <div class="view" data-view="ask"></div>
      <a class="nav-item" data-view="inbox"></a>
      <a class="nav-item" data-view="ask"></a>
    `;
    window.location.hash = "";
  });

  it("exposes known views and defaults to inbox", () => {
    expect(VIEWS).toContain("settings");
    expect(currentView()).toBe("inbox");
  });

  it("reads hash routes", () => {
    window.location.hash = "#/ask";
    expect(currentView()).toBe("ask");
    window.location.hash = "#/nope";
    expect(currentView()).toBe("inbox");
  });

  it("toggles active view and nav on renderRoute", () => {
    window.location.hash = "#/ask";
    renderRoute();
    expect(document.querySelector('.view[data-view="ask"]').classList.contains("active")).toBe(
      true,
    );
    expect(document.querySelector('.nav-item[data-view="ask"]').classList.contains("active")).toBe(
      true,
    );
    expect(document.title).toContain("Ask");
  });

  it("initRouter listens for hashchange", () => {
    initRouter();
    window.location.hash = "#/ask";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(document.querySelector('.view[data-view="ask"]').classList.contains("active")).toBe(
      true,
    );
  });
});
