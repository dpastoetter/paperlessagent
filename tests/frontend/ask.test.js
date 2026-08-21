import { describe, expect, it } from "vitest";

import {
  ASK_EXAMPLES,
  ASK_HISTORY_MAX_TURNS,
  classifyClientEvidence,
  evidenceLabel,
  historyPayloadFromTurns,
  sanitizeDocumentOpenUrl,
  stripSourcesSection,
} from "../../app/static/ask.js";

describe("Ask helpers", () => {
  it("exposes example questions for the empty state", () => {
    expect(ASK_EXAMPLES.length).toBeGreaterThanOrEqual(4);
    expect(ASK_EXAMPLES.some((q) => /Acme/i.test(q))).toBe(true);
  });

  it("strips trailing Sources sections from model replies", () => {
    expect(stripSourcesSection("Answer text\n\n## Sources\n- a.pdf")).toBe("Answer text");
  });

  it("sanitizes open_url to same-origin document file paths", () => {
    expect(sanitizeDocumentOpenUrl("/api/documents/abc/file", "abc")).toBe(
      "/api/documents/abc/file",
    );
    expect(sanitizeDocumentOpenUrl("https://evil.example/x", "abc")).toBe(
      "/api/documents/abc/file",
    );
    expect(sanitizeDocumentOpenUrl("javascript:alert(1)", "abc")).toBe(
      "/api/documents/abc/file",
    );
    expect(sanitizeDocumentOpenUrl("/api/documents/abc/file://x", "abc")).toBe(
      "/api/documents/abc/file",
    );
  });

  it("builds bounded history from completed turns", () => {
    const turns = [
      { id: "1", question: "Q1", reply: "A1", status: "success" },
      { id: "2", question: "Q2", reply: "A2", status: "success" },
      { id: "3", question: "Q3", reply: "A3", status: "success" },
      { id: "4", question: "Q4", loading: true },
    ];
    const history = historyPayloadFromTurns(turns, ASK_HISTORY_MAX_TURNS);
    expect(history.every((h) => h.role === "user" || h.role === "assistant")).toBe(true);
    expect(history.length).toBeLessThanOrEqual(ASK_HISTORY_MAX_TURNS);
    expect(history.at(-2)).toEqual({ role: "user", content: "Q3" });
    expect(history.at(-1)).toEqual({ role: "assistant", content: "A3" });
    expect(history.some((h) => h.content === "Q4")).toBe(false);
  });

  it("classifies evidence and labels weak/none states", () => {
    expect(classifyClientEvidence({ grounded: false })).toBe("none");
    expect(classifyClientEvidence({ evidence: "weak" })).toBe("weak");
    expect(classifyClientEvidence({ evidence: "strong", retrieval_count: 2 })).toBe("strong");
    expect(evidenceLabel("weak", true)).toMatch(/Limited supporting evidence/i);
    expect(evidenceLabel("none", false)).toMatch(/No relevant documents/i);
    expect(evidenceLabel("strong", true)).toBe("");
  });
});
