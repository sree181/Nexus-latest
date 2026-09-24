import { describe, expect, it } from "vitest";

import { canAccess } from "../components/RequiresCapability";
import { landingForIdentity } from "./RoleLanding";

describe("role-aware product entry", () => {
  it("selects a distinct server-authorized workspace for every supported role", () => {
    expect(landingForIdentity({ role: "developer", primary_role: "developer" })).toBe("/developer/sessions");
    expect(landingForIdentity({ role: "analyst", primary_role: "analyst" })).toBe("/analyst/queue");
    expect(landingForIdentity({ role: "ciso", primary_role: "ciso" })).toBe("/ciso/overview");
  });

  it("refuses an inconsistent identity response", () => {
    expect(landingForIdentity({ role: "analyst", primary_role: "ciso" })).toBeNull();
  });

  it("checks the server-issued capability rather than a display label", () => {
    expect(canAccess(["case.read", "case.write"], "case.write")).toBe(true);
    expect(canAccess(["case.read", "case.write"], "policy.write")).toBe(false);
  });
});
