import { describe, expect, it } from "vitest";

import {
  adjacentIndex,
  isDirty,
  isTypingTarget,
  nextIndexAfterRemoval,
  overridesEqual,
  shouldPreserveReviewEditor,
} from "../../app/static/review.js";

describe("nextIndexAfterRemoval", () => {
  it("returns -1 when queue becomes empty", () => {
    expect(nextIndexAfterRemoval(1, 0)).toBe(-1);
  });

  it("keeps the same slot when removing a middle item", () => {
    expect(nextIndexAfterRemoval(3, 1)).toBe(1);
  });

  it("selects the previous item when removing the last", () => {
    expect(nextIndexAfterRemoval(3, 2)).toBe(1);
  });
});

describe("adjacentIndex", () => {
  it("wraps forward and backward", () => {
    expect(adjacentIndex(3, 2, 1)).toBe(0);
    expect(adjacentIndex(3, 0, -1)).toBe(2);
    expect(adjacentIndex(3, 1, 1)).toBe(2);
  });

  it("handles empty lists", () => {
    expect(adjacentIndex(0, 0, 1)).toBe(-1);
  });
});

describe("overridesEqual / isDirty", () => {
  const base = {
    filename: "a.pdf",
    doc_type: "invoice",
    doc_date: "2024-01-01",
    subject: "S",
    counterparties: "Acme",
    reference_ids: ["1", "2"],
    summary: "hi",
    amount: 10,
    currency: "EUR",
  };

  it("treats identical overrides as clean", () => {
    expect(overridesEqual(base, { ...base, reference_ids: ["1", "2"] })).toBe(true);
    expect(isDirty(base, { ...base })).toBe(false);
  });

  it("flags field and reference changes as dirty", () => {
    expect(isDirty(base, { ...base, subject: "Other" })).toBe(true);
    expect(isDirty(base, { ...base, reference_ids: ["1"] })).toBe(true);
    expect(isDirty(base, { ...base, amount: null })).toBe(true);
  });

  it("returns false without a baseline", () => {
    expect(isDirty(null, base)).toBe(false);
  });
});

describe("isTypingTarget", () => {
  it("detects form fields and contenteditable", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const select = document.createElement("select");
    const div = document.createElement("div");
    const editable = document.createElement("div");
    const nested = document.createElement("span");
    editable.contentEditable = "true";
    editable.appendChild(nested);
    document.body.append(input, textarea, select, div, editable);

    expect(isTypingTarget(input)).toBe(true);
    expect(isTypingTarget(textarea)).toBe(true);
    expect(isTypingTarget(select)).toBe(true);
    expect(isTypingTarget(div)).toBe(false);
    expect(isTypingTarget(editable)).toBe(true);
    expect(isTypingTarget(nested)).toBe(true);

    editable.remove();
    input.remove();
    textarea.remove();
    select.remove();
    div.remove();
  });
});

describe("shouldPreserveReviewEditor", () => {
  it("keeps the editor when the same review stays selected", () => {
    expect(shouldPreserveReviewEditor("a", "a", true)).toBe(true);
  });

  it("rebuilds when the queue was empty or selection changes", () => {
    expect(shouldPreserveReviewEditor(null, "a", true)).toBe(false);
    expect(shouldPreserveReviewEditor("a", "b", true)).toBe(false);
    expect(shouldPreserveReviewEditor("a", "a", false)).toBe(false);
  });
});
