import { describe, expect, it } from "vitest";

import {
  DEFAULT_PIPELINE_STEPS,
  hooks,
  knownCategories,
  setKnownCategories,
  workflowState,
} from "../../app/static/state.js";

describe("state", () => {
  it("updates known categories", () => {
    setKnownCategories(["invoice", "other"]);
    expect(knownCategories).toEqual(["invoice", "other"]);
    setKnownCategories(null);
    expect(knownCategories).toEqual([]);
  });

  it("ships default pipeline steps and workflow state", () => {
    expect(DEFAULT_PIPELINE_STEPS[0].id).toBe("read");
    expect(workflowState.steps.length).toBe(DEFAULT_PIPELINE_STEPS.length);
    expect(typeof hooks.refreshInbox).toBe("function");
  });
});
