import { afterEach, describe, expect, it } from "vitest";
import { clearSignInArtifacts, constantTimeStateEquals } from "./auth";

afterEach(() => {
  sessionStorage.clear();
});

describe("OAuth callback state validation", () => {
  it("accepts only the exact persisted state", () => {
    expect(constantTimeStateEquals("expected-state", "expected-state")).toBe(true);
    expect(constantTimeStateEquals("expected-state", "expected-STATE")).toBe(false);
    expect(constantTimeStateEquals("expected-state", "short")).toBe(false);
    expect(constantTimeStateEquals("expected-state", null)).toBe(false);
    expect(constantTimeStateEquals(null, "expected-state")).toBe(false);
  });

  it("removes all transient PKCE artifacts", () => {
    sessionStorage.setItem("meshagent.pkce.verifier", "verifier");
    sessionStorage.setItem("meshagent.pkce.state", "state");
    sessionStorage.setItem("meshagent.pkce.return", "/runs/a");

    clearSignInArtifacts();

    expect(sessionStorage.getItem("meshagent.pkce.verifier")).toBeNull();
    expect(sessionStorage.getItem("meshagent.pkce.state")).toBeNull();
    expect(sessionStorage.getItem("meshagent.pkce.return")).toBeNull();
  });
});
