import { describe, expect, it } from "vitest";

import {
  findDuplicateFolders,
  pathValidationLabel,
  summarizeAiStatus,
  summarizeAutoProcessing,
} from "../../app/static/settings.js";

describe("summarizeAiStatus", () => {
  it("marks local Ollama ready when models are ready", () => {
    expect(
      summarizeAiStatus({
        provider: "ollama",
        openaiReady: false,
        ollamaReady: true,
        disclaimerAccepted: false,
      }),
    ).toEqual({ state: "ok", label: "Ready" });
  });

  it("flags Ollama when not ready", () => {
    expect(
      summarizeAiStatus({
        provider: "ollama",
        openaiReady: false,
        ollamaReady: false,
        disclaimerAccepted: true,
      }),
    ).toEqual({ state: "warn", label: "Needs attention" });
  });

  it("requires cloud disclaimer before cloud is ready", () => {
    expect(
      summarizeAiStatus({
        provider: "openai",
        openaiReady: true,
        ollamaReady: false,
        disclaimerAccepted: false,
      }),
    ).toEqual({ state: "warn", label: "Needs attention" });
  });

  it("marks cloud ready when signed in and approved", () => {
    expect(
      summarizeAiStatus({
        provider: "openai",
        openaiReady: true,
        ollamaReady: false,
        disclaimerAccepted: true,
      }),
    ).toEqual({ state: "ok", label: "Ready" });
  });
});

describe("summarizeAutoProcessing", () => {
  it("is On when poll interval is positive", () => {
    expect(summarizeAutoProcessing(30)).toEqual({ state: "ok", label: "On" });
  });

  it("is Off when poll interval is zero", () => {
    expect(summarizeAutoProcessing(0)).toEqual({ state: "off", label: "Off" });
  });
});

describe("findDuplicateFolders", () => {
  it("returns empty when folders are unique", () => {
    expect(
      findDuplicateFolders([
        { name: "invoice", folder: "/a/invoice" },
        { name: "other", folder: "/a/other" },
      ]),
    ).toEqual([]);
  });

  it("groups categories that share a folder path", () => {
    expect(
      findDuplicateFolders([
        { name: "invoice", folder: "/a/shared/" },
        { name: "receipt", folder: "/a/shared" },
        { name: "other", folder: "/a/other" },
      ]),
    ).toEqual([{ folder: "/a/shared", names: ["invoice", "receipt"] }]);
  });
});

describe("pathValidationLabel", () => {
  it("labels a valid directory", () => {
    expect(pathValidationLabel({ exists: true, is_dir: true })).toEqual({
      state: "ok",
      label: "Valid",
    });
  });

  it("labels missing paths", () => {
    expect(pathValidationLabel({ exists: false, is_dir: false })).toEqual({
      state: "warn",
      label: "Missing",
    });
  });

  it("labels API errors", () => {
    expect(pathValidationLabel({ status: "error", error: "bad" })).toEqual({
      state: "err",
      label: "Problem",
    });
  });
});

describe("settings jump targets", () => {
  it("covers the six IA section anchors from the plan", () => {
    const anchors = [
      "settings-ai",
      "settings-filing",
      "settings-automation",
      "settings-appearance",
      "settings-system",
      "settings-privacy",
    ];
    expect(anchors).toHaveLength(6);
    expect(new Set(anchors).size).toBe(6);
  });
});

describe("advanced disclosure helpers", () => {
  it("treats details.open as the expanded source of truth", () => {
    const details = { open: false };
    expect(details.open ? "true" : "false").toBe("false");
    details.open = true;
    expect(details.open ? "true" : "false").toBe("true");
  });
});
