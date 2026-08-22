import { describe, expect, it } from "vitest";

import {
  adjacentIndex,
  filenameWithEnteredDate,
  isDirty,
  isTypingTarget,
  nextIndexAfterRemoval,
  normalizeReviewDocDate,
  overridesEqual,
  shouldPreserveReviewEditor,
  syncFilenameDatePrefix,
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

describe("filenameWithEnteredDate", () => {
  const undated =
    "undated_Invoice_Blank_Invoice_Template_With_Payment_Fiel_Company_Name_USD0.pdf";

  it("replaces a leading undated prefix once a full date is typed", () => {
    expect(filenameWithEnteredDate(undated, "2024-03-15")).toBe(
      "2024-03-15_Invoice_Blank_Invoice_Template_With_Payment_Fiel_Company_Name_USD0.pdf",
    );
    expect(filenameWithEnteredDate(undated, "2024/03/15")).toBe(
      "2024-03-15_Invoice_Blank_Invoice_Template_With_Payment_Fiel_Company_Name_USD0.pdf",
    );
  });

  it("leaves undated filenames alone until the date is complete", () => {
    expect(filenameWithEnteredDate(undated, "2024")).toBe(undated);
    expect(filenameWithEnteredDate(undated, "2024-03")).toBe(undated);
    expect(filenameWithEnteredDate(undated, "2024-13-01")).toBe(undated);
  });

  it("does not rewrite custom filenames that are not undated", () => {
    expect(filenameWithEnteredDate("scan.pdf", "2024-03-15")).toBe("scan.pdf");
    expect(filenameWithEnteredDate("2023-01-01_Invoice.pdf", "2024-03-15")).toBe(
      "2023-01-01_Invoice.pdf",
    );
  });

  it("keeps updating a prefix that this field auto-applied", () => {
    expect(
      filenameWithEnteredDate("2024-03-15_Invoice.pdf", "2024-04-01", "2024-03-15"),
    ).toBe("2024-04-01_Invoice.pdf");
    expect(filenameWithEnteredDate("2024-03-15_Invoice.pdf", "", "2024-03-15")).toBe(
      "undated_Invoice.pdf",
    );
  });
});

describe("normalizeReviewDocDate", () => {
  it("accepts ISO and slash dates and rejects impossible days", () => {
    expect(normalizeReviewDocDate(" 2024/3/15 ")).toBe(null);
    expect(normalizeReviewDocDate("2024/03/15")).toBe("2024-03-15");
    expect(normalizeReviewDocDate("2024-02-30")).toBe(null);
  });
});

describe("syncFilenameDatePrefix", () => {
  it("writes the date into an undated filename and tracks the auto prefix", () => {
    const input = document.createElement("input");
    input.value = "undated_Invoice.pdf";
    expect(syncFilenameDatePrefix(input, "2024-08-22")).toBe(true);
    expect(input.value).toBe("2024-08-22_Invoice.pdf");
    expect(input.dataset.autoDatePrefix).toBe("2024-08-22");
    expect(syncFilenameDatePrefix(input, "2024-09-01")).toBe(true);
    expect(input.value).toBe("2024-09-01_Invoice.pdf");
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
