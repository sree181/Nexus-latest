import { describe, expect, it } from "vitest";

import { reviewRequestIdFromCaseFinding } from "./AnalystCaseDetail";

describe("Analyst case source routing", () => {
  it("extracts the review request ID for cases escalated from Developer review", () => {
    expect(reviewRequestIdFromCaseFinding("review:rev_1790357603397_0df21261db3e")).toBe(
      "rev_1790357603397_0df21261db3e",
    );
  });

  it("leaves native findings on the investigation route", () => {
    expect(reviewRequestIdFromCaseFinding("sink:security:unsafe-call")).toBeNull();
    expect(reviewRequestIdFromCaseFinding("review:")).toBeNull();
  });
});
