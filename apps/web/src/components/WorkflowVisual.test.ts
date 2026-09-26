import { describe, expect, it } from "vitest";

import { ApiError } from "../lib/api";
import { isVersionConflict } from "./WorkflowVisual";

describe("workflow conflict recovery", () => {
  it("recognizes only HTTP 412 as a stale-write conflict", () => {
    expect(isVersionConflict(new ApiError(412, "record changed"))).toBe(true);
    expect(isVersionConflict(new ApiError(403, "forbidden"))).toBe(false);
    expect(isVersionConflict(new Error("record changed"))).toBe(false);
  });
});
