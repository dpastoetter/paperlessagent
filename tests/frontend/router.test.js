import { beforeEach, describe, expect, it } from "vitest";

import {
  VIEWS,
  currentView,
  initRouter,
  parseHashQuery,
  renderRoute,
  setHashQuery,
} from "../../app/static/router.js";

describe("router", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="view" data-view="inbox"></div>
      <div class="view" data-view="ask"></div>
      <div class="view" data-view="archive"></div>
      <a class="nav-item" data-view="inbox"></a>
      <a class="nav-item" data-view="ask"></a>
      <a class="nav-item" data-view="archive"></a>
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

  it("ignores query string when resolving the view name", () => {
    window.location.hash = "#/archive?q=Acme&doc_type=invoice";
    expect(currentView()).toBe("archive");
  });

  it("parses and writes hash query params", () => {
    window.location.hash = "#/archive?q=Acme&doc_type=invoice";
    expect(parseHashQuery()).toEqual({ q: "Acme", doc_type: "invoice" });

    setHashQuery("archive", { q: "Beta", date_from: "2024-01-01", empty: "" }, { replace: true });
    expect(currentView()).toBe("archive");
    expect(parseHashQuery()).toEqual({ q: "Beta", date_from: "2024-01-01" });
  });

  it("toggles active view and nav on renderRoute", () => {
    window.location.hash = "#/ask";
    renderRoute();
    expect(document.querySelector('.view[data-view="ask"]').classList.contains("active")).toBe(
      true,
    );
    const askNav = document.querySelector('.nav-item[data-view="ask"]');
    expect(askNav.classList.contains("active")).toBe(true);
    expect(askNav.getAttribute("aria-current")).toBe("page");
    expect(
      document.querySelector('.nav-item[data-view="inbox"]').getAttribute("aria-current"),
    ).toBeNull();
    expect(document.title).toContain("Ask");
  });

  it("moves aria-current when the route changes", () => {
    window.location.hash = "#/inbox";
    renderRoute();
    expect(
      document.querySelector('.nav-item[data-view="inbox"]').getAttribute("aria-current"),
    ).toBe("page");
    window.location.hash = "#/archive";
    renderRoute();
    expect(
      document.querySelector('.nav-item[data-view="inbox"]').getAttribute("aria-current"),
    ).toBeNull();
    expect(
      document.querySelector('.nav-item[data-view="archive"]').getAttribute("aria-current"),
    ).toBe("page");
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
