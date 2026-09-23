import { describe, expect, it } from "vitest";

import { canReadFleetImpact } from "./RunSupply";

describe("RunSupply fleet-impact authorization", () => {
  it("does not request organization-wide impact for a Developer", () => {
    expect(
      canReadFleetImpact(["run.own", "package.gate"], "CVE-2026-0001"),
    ).toBe(false);
  });

  it("allows Analyst and CISO capability sets to request impact", () => {
    expect(canReadFleetImpact(["fleet.read"], "CVE-2026-0001")).toBe(true);
  });

  it("does not request impact when the run has no advisory", () => {
    expect(canReadFleetImpact(["fleet.read"], undefined)).toBe(false);
  });
});
