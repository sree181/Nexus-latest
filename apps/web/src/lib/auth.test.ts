import { afterEach, describe, expect, it, vi } from "vitest";

import { authHeaders, browserSession, localIdentity, socketProtocols } from "./auth";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("browser session boundary", () => {
  it("treats a 401 session response as reauthentication, not an application error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ authenticated: false, expires_at: null, reauth_required: true }),
      { status: 401, headers: { "content-type": "application/json" } },
    )));

    await expect(browserSession()).resolves.toEqual({
      authenticated: false,
      expires_at: null,
      reauth_required: true,
    });
  });

  it("surfaces an unavailable session service", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("error", { status: 503 })));
    await expect(browserSession()).rejects.toThrow("could not verify");
  });
});

describe("explicit local development identity", () => {
  it("defaults to an unprivileged developer and never invents an unsupported role", () => {
    localStorage.setItem("meshagent.local.role", "administrator");
    expect(localIdentity()).toEqual({ user: "dev@localhost", role: "developer" });
    expect(authHeaders()).toEqual({
      "X-MeshAgent-User": "dev@localhost",
      "X-MeshAgent-Role": "developer",
    });
  });

  it("keeps local WebSocket identity out of the URL", () => {
    localStorage.setItem("meshagent.local.user", "priya@example.com");
    localStorage.setItem("meshagent.local.role", "analyst");
    const protocols = socketProtocols();
    expect(protocols?.[0]).toBe("meshagent.local");
    expect(protocols?.join(".")).not.toContain("priya@example.com");
  });
});
