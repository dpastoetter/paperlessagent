import { describe, expect, it } from "vitest";

import { shouldRefreshReviews } from "../../app/static/events.js";

describe("shouldRefreshReviews", () => {
  it("refreshes as soon as a file is waiting for review", () => {
    expect(shouldRefreshReviews({ type: "file_finished", status: "review" })).toBe(
      true,
    );
  });

  it("still refreshes when the whole job finishes", () => {
    expect(shouldRefreshReviews({ type: "job_finished", status: "success" })).toBe(
      true,
    );
  });

  it("ignores in-progress and non-review finishes", () => {
    expect(shouldRefreshReviews({ type: "file_started" })).toBe(false);
    expect(shouldRefreshReviews({ type: "file_finished", status: "done" })).toBe(
      false,
    );
    expect(shouldRefreshReviews({ type: "file_finished", status: "error" })).toBe(
      false,
    );
    expect(shouldRefreshReviews({ type: "step", step_id: "review" })).toBe(false);
    expect(shouldRefreshReviews(null)).toBe(false);
  });
});
