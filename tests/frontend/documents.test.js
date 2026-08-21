import { describe, expect, it } from "vitest";

import {
  ARCHIVE_MIN_QUERY_LEN,
  archiveListMessage,
  buildDocumentsQuery,
  countActiveFilters,
  emptyFilters,
  filtersEqual,
  filtersFromHashQuery,
  filtersToHashParams,
  normalizeFilters,
  requestKey,
  shouldAutoSearchQuery,
} from "../../app/static/documents.js";

describe("archive filter helpers", () => {
  it("normalizes and counts active filters", () => {
    const filters = normalizeFilters({
      q: "  Acme ",
      doc_type: "invoice",
      counterparty: "",
      date_from: "2024-01-01",
      date_to: "  ",
    });
    expect(filters.q).toBe("Acme");
    expect(filters.date_to).toBe("");
    expect(countActiveFilters(filters)).toBe(3);
    expect(countActiveFilters(emptyFilters())).toBe(0);
  });

  it("round-trips filters through hash params", () => {
    const filters = normalizeFilters({
      q: "Acme",
      doc_type: "invoice",
      counterparty: "Corp",
      date_from: "2024-01-01",
      date_to: "2024-12-31",
    });
    const params = filtersToHashParams(filters, "demo-doc-1");
    expect(params).toEqual({
      q: "Acme",
      doc_type: "invoice",
      counterparty: "Corp",
      date_from: "2024-01-01",
      date_to: "2024-12-31",
      doc: "demo-doc-1",
    });
    expect(filtersFromHashQuery(params)).toEqual(filters);
  });

  it("compares filters and builds stable request keys", () => {
    const a = normalizeFilters({ q: "Acme", doc_type: "invoice" });
    const b = normalizeFilters({ q: "Acme", doc_type: "invoice" });
    const c = normalizeFilters({ q: "Beta", doc_type: "invoice" });
    expect(filtersEqual(a, b)).toBe(true);
    expect(filtersEqual(a, c)).toBe(false);

    const params = buildDocumentsQuery(a, { limit: 40, offset: 0 });
    expect(params.limit).toBe("40");
    expect(params.q).toBe("Acme");
    expect(requestKey(params)).toContain("q=Acme");
  });

  it("gates auto-search on query length", () => {
    expect(shouldAutoSearchQuery("")).toBe(true);
    expect(shouldAutoSearchQuery("a")).toBe(false);
    expect(shouldAutoSearchQuery("ab")).toBe(true);
    expect(ARCHIVE_MIN_QUERY_LEN).toBe(2);
  });
});

describe("archiveListMessage", () => {
  it("distinguishes empty archive, no results, error, and loading", () => {
    expect(
      archiveListMessage({
        documents: [],
        filters: emptyFilters(),
        error: null,
        loading: false,
      }).kind,
    ).toBe("empty");

    expect(
      archiveListMessage({
        documents: [],
        filters: normalizeFilters({ q: "zzz" }),
        error: null,
        loading: false,
      }).kind,
    ).toBe("no-results");

    expect(
      archiveListMessage({
        documents: [],
        filters: emptyFilters(),
        error: "boom",
        loading: false,
      }).kind,
    ).toBe("error");

    expect(
      archiveListMessage({
        documents: [],
        filters: emptyFilters(),
        error: null,
        loading: true,
      }).kind,
    ).toBe("loading");

    expect(
      archiveListMessage({
        documents: [{ id: "1" }],
        filters: emptyFilters(),
        error: null,
        loading: false,
      }).kind,
    ).toBe("results");
  });
});
