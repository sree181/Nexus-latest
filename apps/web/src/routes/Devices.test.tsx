import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Device } from "./Devices";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    api: { ...original.api, revokeDevice: vi.fn() },
  };
});

const device = {
  id: "device-1",
  label: "Work laptop",
  subject: "user-1",
  name: "A User",
  role: "developer" as const,
  created_at: 1_700_000_000,
  last_used: 1_700_000_100,
  verified: true,
};

function renderDevice(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ul><Device device={device} /></ul>
    </QueryClientProvider>,
  );
}

describe("device revoke confirmation", () => {
  it("requires an explicit confirmation and allows cancellation", () => {
    renderDevice();

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(screen.getByText("Stop this machine from recording?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm revoke" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Confirm revoke" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });
});
