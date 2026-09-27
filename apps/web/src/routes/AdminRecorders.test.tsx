import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RecorderDetail } from "../lib/api";
import { RecorderInspector, RecorderTrustActions } from "./AdminRecorders";

const { quarantineRecorder, revokeRecorder } = vi.hoisted(() => ({
  quarantineRecorder: vi.fn(),
  revokeRecorder: vi.fn(),
}));

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, api: { ...original.api, quarantineRecorder, revokeRecorder } };
});

const recorder: RecorderDetail = {
  id: "erd_1",
  label: "Maya work laptop",
  credential_kind: "dpop",
  subject: "maya-1",
  name: "Maya",
  email: "maya@example.com",
  verified: true,
  deployment: "acme-prod",
  platform: "darwin",
  platform_version: "15.0",
  architecture: "arm64",
  recorder_version: "1.1.0",
  key_thumbprint: "thumbprint",
  attestation_format: "none",
  trust_state: "active",
  version: 7,
  config_version: 2,
  reported_config_version: 2,
  config_status: "current",
  config_digest: "a".repeat(64),
  enrolled_at: 1_700_000_000,
  approved_at: 1_700_000_010,
  last_seen_at: 1_700_000_100,
  queue_batches: 0,
  queue_bytes: 0,
  oldest_queued_age_seconds: 0,
  adapter_state: "ready",
  delivery_state: "healthy",
  legacy_retire_at: null,
  history: [],
};

function renderActions() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><RecorderTrustActions recorder={recorder} /></QueryClientProvider>);
}

describe("enterprise recorder trust actions", () => {
  it("sends the observed version and a recorded quarantine reason", async () => {
    quarantineRecorder.mockResolvedValue({ receipt_id: "rr_1" });
    renderActions();
    fireEvent.click(screen.getByRole("button", { name: "Quarantine" }));
    const confirm = screen.getByRole("button", { name: "Confirm quarantine" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason for quarantine"), { target: { value: "Unexpected recorder binary" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(quarantineRecorder).toHaveBeenCalledWith("erd_1", {
      expected_version: 7,
      reason: "Unexpected recorder binary",
    }));
  });

  it("discloses that revocation is terminal before confirmation", () => {
    renderActions();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(screen.getByText(/Revocation is terminal/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm revoke" })).toBeDisabled();
  });

  it("distinguishes desired policy from the last configuration actually applied", () => {
    const stale = { ...recorder, config_version: 3, reported_config_version: 2, config_status: "stale" as const };
    render(<QueryClientProvider client={new QueryClient()}><RecorderInspector recorder={stale} canWriteTrust={false} /></QueryClientProvider>);
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText("v2 · stale")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/newer signed policy is verified but not active/i);
  });
});
