import { describe, expect, it } from "vitest";

import { enrollmentCodeFromLocation } from "./RecorderEnrollment";

describe("recorder enrollment deep links", () => {
  it("normalizes a valid one-time code", () => {
    expect(enrollmentCodeFromLocation("?code=nhdg-326z")).toBe("NHDG-326Z");
  });

  it("ignores malformed or missing codes", () => {
    expect(enrollmentCodeFromLocation("?code=ABCD-01I5")).toBe("");
    expect(enrollmentCodeFromLocation("?next=%2Fadmin")).toBe("");
  });
});
