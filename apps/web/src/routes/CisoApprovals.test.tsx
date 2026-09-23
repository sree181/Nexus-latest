import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApprovalDecisionPanel } from "./CisoApprovals";

const { decideApproval } = vi.hoisted(() => ({ decideApproval: vi.fn() }));

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, api: { ...original.api, decideApproval } };
});

vi.mock("../lib/useIdentity", () => ({
  useIdentity: () => ({
    me: {
      subject: "ciso-reviewer",
      name: "Alex",
      email: "alex@example.com",
      role: "ciso",
      primary_role: "ciso",
      capabilities: ["exception.approve"],
      verified: true,
    },
  }),
}));

const approval = {
  id: "apr_1",
  kind: "policy_exception",
  resource_id: "exc_1",
  requester: "analyst-requester",
  requester_name: "Priya",
  status: "pending",
  rationale: "Vendor replacement needs seven days.",
  approver: null,
  decision_rationale: null,
  version: 1,
  expires_at: 1_800_000_000,
  created_at: 1_700_000_000,
  decided_at: null,
};

describe("CISO approval decision", () => {
  it("requires a rationale and sends the observed version", async () => {
    decideApproval.mockResolvedValue({ ...approval, status: "approved", version: 2 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ApprovalDecisionPanel approval={approval} /></QueryClientProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const confirm = screen.getByRole("button", { name: "Confirm approve" });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText("approve rationale"), {
      target: { value: "Controls are measurable and the request is time bounded." },
    });
    fireEvent.click(confirm);

    await waitFor(() => expect(decideApproval).toHaveBeenCalledWith("apr_1", {
      expected_version: 1,
      decision: "approve",
      rationale: "Controls are measurable and the request is time bounded.",
    }));
  });
});
